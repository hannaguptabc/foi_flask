from application import application
from flask import render_template,session, redirect ,url_for,request, send_file
from io import BytesIO
from docx import Document
from typing import Optional
import psycopg2
# from passlib.context import CryptContext
import datetime
from openai import AzureOpenAI
from openai import AsyncAzureOpenAI
import os
import uuid
import logging

from dotenv import load_dotenv
import bcrypt
import asyncio
import ast
import fitz
import json
from flask import g
from flask import jsonify
from flask import Flask
from langchain_community.vectorstores.pgvector import PGVector
from langchain_openai.embeddings import AzureOpenAIEmbeddings
from logging.handlers import RotatingFileHandler
load_dotenv('myenv/.env')
application.secret_key='32qwe34ds'

log_file = 'app.log'
file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=10)
# Set the formatter
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
# application.config['CACHE_TYPE'] = 'simple'  # Use a simple in-memory cache for demonstration purposes
# application.config['CACHE_DEFAULT_TIMEOUT'] = 300 
application.config['SESSION_TYPE'] = 'filesystem'
application.config['SESSION_PERMANENT'] = False
application.config['PERMANENT_SESSION_LIFETIME'] = datetime.timedelta(minutes=120)
application.config['SESSION_USE_SIGNER'] = True

client=AzureOpenAI(api_key = os.getenv("openai_api_key"),
                   api_version="2023-09-01-preview",
                   azure_endpoint=os.getenv("openai_api_base"))

# pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

logging.basicConfig(level=logging.DEBUG)
logging.getLogger().addHandler(file_handler)
# Set the logging level
logging.getLogger().setLevel(logging.INFO)

CONNECTION_STRING = PGVector.connection_string_from_db_params(
    driver=os.environ.get("PGVECTOR_DRIVER", "psycopg2"),
    host=os.environ.get("PGHOST"),
    port=os.environ.get("PGPORT"),
    database=os.environ.get("PGDATABASE"),
    user=os.environ.get("PGUSER"),
    password=os.environ.get("PGPASSWORD")
)



EMBEDDINGS = AzureOpenAIEmbeddings(
    azure_endpoint=os.getenv('AZURE_OPENAI_ENDPOINT'),
    openai_api_key=os.getenv('AZURE_OPENAI_API_KEY'),
    openai_api_version='2023-09-01-preview'
)
NAMESPACE = "pgvector/foi_corpus"
COLLECTION_NAME = 'FOI-CORPUS'

vectorstore = PGVector(
        collection_name=COLLECTION_NAME,
        connection_string=CONNECTION_STRING,
        embedding_function=EMBEDDINGS,
    )



class User:
    def __init__(self, id: int, username: str, hashed_password: str, created_at: datetime):
        self.id = id
        self.username = username
        self.hashed_password = hashed_password
        self.created_at = created_at
def get_db_connection():
    keepalive_kwargs = {
    "keepalives": 1,
    "keepalives_idle": 30,
    "keepalives_interval": 5,
    "keepalives_count": 5,
}
    return psycopg2.connect(
        dbname=os.environ.get('FOI_DATABASE'),
        user=os.environ.get('user'),
        password=os.environ.get('pgfoipassword'),
        host=os.environ.get('pgfoihost'),
        port=os.environ.get('pgfoiport'),
        **keepalive_kwargs

    )

def hash_password(password):
    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
    return hashed_password.decode('utf-8')

def create_user(username: str, hashed_password: str, created_at: datetime):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO users (username, hashed_password, created_at) VALUES (%s, %s, %s)",
        (username, hashed_password, created_at)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_user_by_username(username: str) -> Optional[User]:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, username, hashed_password, created_at FROM users WHERE username = %s", (username,))
    user_record = cursor.fetchone()
    cursor.close()
    conn.close()
    print("completed")
    if user_record:
        return User(*user_record)
    return None

# Verify password
def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())

# Authenticate user
def authenticate_user(username: str, password: str):
    user = get_user_by_username(username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

def safe_literal_eval(data):
    try:
        return ast.literal_eval(data)
    except (SyntaxError, ValueError):
        # If ast.literal_eval() fails, resort to splitting by commas and stripping whitespace
        return [item.strip() for item in data.strip("[]").split(",")]
    

def parse_terminal_dict(output):
    import re
 
    # This pattern looks for the outermost curly braces that form a dictionary
    # It does not handle nested dictionaries well
    pattern = r'\{\s*[^{}]*\}'
 
    match = re.search(pattern, output, re.DOTALL)  # re.DOTALL allows '.' to match newline characters
 
    if match:
        extracted_dict = match.group(0)
    else:
        extracted_dict = output  # Returns an empty dictionary if no match is found
 
    return extracted_dict

def read_pdf_mupdf(file_path):
    text = ""

    with fitz.open(file_path) as pdf_document:
        for page_number in range(pdf_document.page_count):
            page = pdf_document[page_number]
            text += page.get_text()

    return text


def get_requests_by_email(email_id: str, latest_timestamp: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Query all requests for a particular email ID
        query = f'''
            SELECT *
            FROM requests
            WHERE email_id = %s
            AND timestamp < %s;
        '''
        cursor.execute(query, (email_id, latest_timestamp))

        # Fetch all the rows
        rows = cursor.fetchall()

        # Close the connection
        conn.close()
        print("-----------------")
        print(rows)
        print("------------------------")

        return rows

    except Exception as e:
        print(f"Error: {e}")
        # Handle the error as needed
        return None


def insert_request(request_text: str, timestamp: str, email_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Generate a UUID for the request_id
        request_id = str(uuid.uuid4())

        # Insert a new request into the 'request' table
        query = '''
            INSERT INTO request (request_id, request_text, timestamp, email_id)
            VALUES (%s, %s, %s, %s);
        '''
        cursor.execute(query, (request_id, request_text, timestamp, email_id))

        # Commit the transaction
        conn.commit()

        # Close the connection
        conn.close()
        print("Request inserted successfully")

    except Exception as e:
        print(f"Error: {e}")
        # Rollback the transaction if an error occurs
        conn.rollback()



async def check_full_name(text: str):
    content = f'''Act as an Freedom of Information responder, and check if the the request given to you, contains the full name of the requester.
    You just need to return True or False.
    In case the request contains the whole name, including firstname, middle name(optional) and last name, you should return True.
    In case the request contains an honorific(such as Mr. or Mrs. or Dr. or Miss) and the last time along with it, then you should return True.
    In case the request only contains the first name of a person or a nickname then you need to return False.
    For example if the request contains "Tom Cruise" then return True, if the text contains "Mr. Cruise" then also return True but if it contains "Tom" then return False.
    The request you need to check for the name is given ahead enclosed in double qoutes="{text}"
'''
    response =client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    
    return response.choices[0].message.content


async def identify_part(text: str):
    print("checks")

    content = f''' DO NOT RETURN ANYTHING OTHER THAN WHAT IS ASKED AHEAD. I want you to return a list always.
    You need to act like a FOI request responder and just divide the following request into different components if the request is asking for multiple pieces of information, and return as a python list of strings otherwise If request only has one piece of information asked just return the whole request as it is if there is a threat or a deadline attach it with request 
    There could be there are multiple lines but they are asking for the same piece of information, just that they are trying to give more refernce so keep that toegether.
    
    I am giving two examples on how to assess.


   example 1:
   "Can you provide me with the details about EOTAS. I need it in 2 days otherwise I will come to your office" 
   now this request is asking for one thing so no need to divide it just return back the whole thing as it is "Can you provide me with the details about EOTAS. I need it in 2 days otherwise I will come to your office"

   example 2: 
   "I would be most grateful if you would provide me, under the Freedom of Information Act, details in respect to the contract below:The details we require are:
   • What Enforcement Contracts for On and Off Street Parking do you have that are currently active including their contract start and end dates, where the end date of the contract is in the future.• Suppliers who applied for inclusion on each framework/contract and were successful & not successful at the PQQ & ITT stages."
   now this request has mainly two components like so return a list like follows:
   ['What Enforcement Contracts for On and Off Street Parking do you have that are currently active including their contract start and end dates, where the end date of the contract is in the future.',
   'Suppliers who applied for inclusion on each framework/contract and were successful & not successful at the PQQ & ITT stages.']

   the request is  [{text}].
   JUST RETURN THE LIST ITSELF NO EXTRA QOUTATIONS OR WORDS.
    '''
    

    response = client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    
    return response.choices[0].message.content

async def validation_of_request(contents: list):
    await asyncio.sleep(1)
    print("inside valid func")

    with open('triaging/1.1/validation.txt', 'r') as file:
    # Read the entire content of the file
        valid = file.read()
    

    content=f''' DO NOT RETURN ANYTHING OTHER THAN WHAT IS ASKED.
    You need to act like a Freedom of Information request responder. This is a list of contents asked in FOI request enclosed in [] [{contents}], you do not need to change this list or components in this just keep it static, and produce the following result.
    The specific rules and regulations for considering what a valid FOI request are as follows: "{valid}"
    You need to go through each component of the list and check whether it is a valid FOI request under the given documentation above or does it come under any exemptions, you need to mention that to.
   I want the json object to be structured like this:
    {{
        "Component of the list provided": {{
        True/False (True in case the request is a valid request and not a subject access request(SAR) or Environmental Information (EIR) requests, give False if it is not a valid request based on the above criteria or it is a EIR or SAR):"Reason for the result(Here you basically need to justify why you think it is a valid request or invalid or SAR or EIR)"
        }},

        }}
Remember to only verify if the information asked in request is valid or not, the vexatious request would be checked later so leave them.
JUST RETURN THE JSON OBJECT ITSELF NO EXTRA WORDS OR QOUTATIONS.
'''
    response =client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    print("ending valid funct")
    return response.choices[0].message.content


async def check_for_vexatious(text: list):
    vexatious_file_path = "triaging/1.2/section-14-dealing-with-vexatious-requests-0-0.pdf"
    pdf_text = read_pdf_mupdf(vexatious_file_path)
    content = f'''
YYou are a Freedom of Information request responder, I will be giving you a Freedom of Information request and you need to process it whether that particular content can be considered vexatious or not.
The request is as follows enclosed in square brackets:[ {text} ]
and the document containing the vexatiuous rules and regulations is in "" , "{pdf_text}".
You need to give it in a dictionary format with the boolean value( whether the request is vexatious or not. You will return True if the request is not vexatious and False if the request is vexatious) as the key and the value of the dictionary will be the reason why you think the request can be considered vexatious or not.
When you are giving the reason for putting a request vexatious remember that the meaning of vexatious request is:
[Vexatious requests refer to repeated and persistent demands or inquiries that are intended to annoy, harass, or cause frustration to the recipient.
These requests often go beyond the bounds of normal and reasonable communication, becoming a form of harassment or disruption. 
In various contexts, such as legal proceedings, administrative processes, or customer service interactions, vexatious requests can impede the normal functioning of the system and create a burdensome or hostile environment for those involved. 
Dealing with vexatious requests may involve establishing clear boundaries, implementing procedures to address repetitive behavior, or, in extreme cases, taking legal measures to prevent ongoing harassment.]
I want it in a dictionary format like given below for each component of the list:
    {{
        True/False(True in case request is not vexatious and False in case it is vexatious): "Reason for being a vexatious request or not being a vexatious request."
        }}
DO NOT GIVE ANYTHING BUT THE DICTONARY.
'''
    
    response =client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    return response.choices[0].message.content


async def refusal_notice(reason, request):
    refusal_file_path='triaging/1.4/Refusal notice - guidance.pdf'
    pdf_text=read_pdf_mupdf(refusal_file_path)
    content = f''' DO NOT RETURN ANYTHING OTHER THAN WHAT IS ASKED AHEAD.
    You need to act like a FOI request responder and generate a refusal notice according to these regulation {pdf_text} after the request been rejected for the folloing reason "{reason}". The request is in [], [{request}]. So state the reason why it is {type} of request.

    '''
    response =client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    return response.choices[0].message.content

async def check_for_repeated(previous_requests, request_list):


    repeated_file_path='triaging/1.3/Dealing with repeat requests.pdf'
    pdf_text=read_pdf_mupdf(repeated_file_path)
    content = f''' DO NOT RETURN ANYTHING OTHER THAN WHAT IS ASKED AHEAD.
    You need to act like a FOI request responder and go through the following rules and regulations regarding repeated FOI requests:[{pdf_text}].
    I am providing you with the previous requests made by the same citizen, you need to analyze the context of the previous request with respect the current list of requests and tell if they are asking for the same piece of information.
    The previous requests and the other details about it are given in [] ahead: [{previous_requests}]
    Now i am giving you the current list of requests made which are: [{request_list}]
    If there is no previous requests provided that means theres no repetead request so just tag those requests as not repeated request.
    I want it in a dictionary format like given below for each component of the current request list:
    {{
        "Component of the current list provided": "If the request is repeated then you need to give "Repeated Request" along with an explanation and proof of the previous request made otherwise just say "Not a repeated request""
        }}
IF THERE IS NO PREVIOUS CONTEXT PROVIDED JUST MARK THAT REQUEST AS NOT REPEATED REQUEST BUT DO NOT GIVE ANYTHING BUT THE DICTONARY.

    '''
    response =client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    return response.choices[0].message.content

async def check_for_completeness(request):
    print("checks")
    # vectorstore = PGVector(
    #         collection_name=COLLECTION_NAME,
    #         connection_string=CONNECTION_STRING,
    #         embedding_function=EMBEDDINGS,
    #     )

    docs =await vectorstore.asimilarity_search_with_score(request)
    

    content=f'''You will be provided with a request and some documents related to it, give me in percentage how well you can answer the request through those given documents.
      The request asking for information is = "{request}".
      The documents of text required to answer the request is given ahead = "{docs}"
      DO NOT GIVE ANYTHING OTHER THAT THE PERCENTAGE ITSELF.
      I REPEAT ONLY PRINT THE PERCENTAGE FOR EXAMPLE "100%" or "56%".
    '''
    response=client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    return response.choices[0].message.content


async def get_response(request_comp, docs):
    content=f'''Do not return anything except the json object. You need to act as an Freedom of Information responder. I will be providing you with the request component, and a dictionary of the most similar chunks of content found in the database along with some medatadata about any exemptions that can be applied on the data. The given dictionary will be in the form of {{"chunk of data":"exemption:exemption applied of the data"}}
                     Now you need to give back a json output, with the request along with its response(the framed response should not contain any of the redactions line that are present in the ) and then if there are any exemptions present in the metadata then you need to give the name of the section of exemption  and also the redaction part that need to be removed from the response under that exemption.
                     Do not return anything but the json itself.
                     The request component is given ahead:'{request_comp}'.
                     the dictionary of similar chunk and exemptions present: '{docs}'. Now you need to go through this dictionary carefully while writing the response, in the dictionary the specific part is mentioned where the exemption is actually present, so now I want you to use your intelligence to scan through the response and see if that exemption related content is present in the response, if it is then only include the exemption otherwise don't. There might be some cases that from the dictionary provided before the chunk has some exemptions attached to it, but when you go through the response , the response does not contain that particular data.
                     The json should be structure in the following way:
                     {{
  "request": "The request component comes here",
  "response": "Here you need to form the response to the Freedom of Information request from the chunks."
  
  "exemptions":{{
      "Section of exemption present":"Evidence: The exact sentence or content that needs to be redacted from the response",
      "Section of exemption present":"Evidence: The exact sentence or content that needs to be redacted from the response",
    }}
  
}}
There could be multiple exemptions then put the key value pairs in the dictionary.
If there are no exemptions, keep the dictionary empty. 
DO NOT RETURN ANYTHING BUT THE JSON OBJECT ITSELF, NO EXTRA WORDS OR QOUTATIONS
'''
    response =client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role": "system", "content": content}]
    )
    return response.choices[0].message.content

async def generate_acknowledgement_letter(request, valid_dict, vex_dict, repeat_dict, ):
    content = f''' You have to act as an FOI request responder and generate an acknowledgement letter to the requester stating that their request has been recieved and is in in process based on a template that I will provide you.
Along with this I will be providing you with dictionaries representing the request and the checks that have been performed on the request. You need to go through all the dictionaries, first dictionary is for validation check you need to see which part of the request is valid and not valid and if it is not valid then give the reson in the acknowledgement letter.
Second dictionary will be to check for vexatiousness, check if there are any vexatious rerquest and give the reason in the letter.
Third dictionary will be for repeated request and if there are any repeated components in the requrst then write the reason in the letter.
The request is provided ahead: '{request}'.
The validation check dictionary is provided ahead: '{valid_dict}'.
The vexatiousness check dictionary is provided ahead: '{vex_dict}'.
The repeated check dictionary is provided ahead: '{repeat_dict}'.
Now you need to frame the letter in such a way that you mention all the parts and then point out to the parts that are vexatious or not valid or repeated, otherwise just say the request is acknowledged.


'''

    response= client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role":"system", "content":content}]
    )
    return response.choices[0].message.content

async def check_for_opinioted(request_list):
    await asyncio.sleep(3)
    print("in the op function")
    content = f'''
You have to act as an FOI request responder and check if the given question from a particular request is asking for a Request handler's opinion, in that case you need to return a json object as follows:
{{
        "Component of the list provided": {{
        True/False (True if it is not asking for an opinion False if it is asking for an opinion) : "Reason for you answer(here you need to give an explanation why you think this request is asking for an opinion or not.)"
        }},

        }}
The list of request is enclosed in the [] ahead: [{request_list}]


DO NOT RETURN ANYTHING BUT THE JSON OBJECT ITSELF.
'''
    response= client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role":"system", "content":content}]
    )
    print(response.choices[0].message.content)

    return response.choices[0].message.content




async def generate_response_letter(request, responses_json):

    response_template='''Dear [Requester's Name], 

Thank you for your request for information under the Freedom of Information Act 2000.  

If exemptions have been applied, we have provided a rationale and details of the exemption. 

Please find below the details of your request alongside a response.  

Question #1 

Response #1 (if exemptions or redactions have been applied, provide a brief description) 

Question #2 

Response #2 (if exemptions or redactions have been applied, provide a brief description) 

Question #3 

Response #3 (if exemptions or redactions have been applied, provide a brief description) 

*** Continues depending on the number of identified questions **** 

 

Should you have any further queries, please re-submit a formal Freedom of Information request through the following front door ***email address of Council’s front door**** 

 

Yours sincerely,  

*** name of council worker*** 

*** work title of council worker*** 

*** name of council *** '''


    content=f'''You have to act as an Freedom of Information request responder and generate a response letter to a request based on a template and also assess if there are any exemptions present.
    I will be giving you three things: the original FOI request, response template, and a list of json object which contains the component of request asking some information, the generated response for it. 
    The original FOI request is as follows: ({request}).
    The response template is as follows: ({response_template})
    The list of json object is as follows: ({responses_json}).


'''
    
    response=client.chat.completions.create(
        model="gpt-4-1106",
        messages=[{"role":"system", "content":content}]
    )
    return response.choices[0].message.content


async def get_department(text: str):
    content=f'''
You have to act as an Freedom of Information request responder and redirect a request to the appropriate department.
I will be providing you a part of the request and you need to assess which department could have the information required to answer to it according to the department list and description I will provide you.
The description and departments are as follows:

FOI Department Description 

 

Adult Services - Oversees social care and support services for adults, including the elderly and those with disabilities, ensuring they have access to necessary resources and assistance. 

 

Children's Services - Responsible for safeguarding and promoting the welfare of children and young people. This includes child protection, foster care, adoption services, and support for families. 

 

Education Services - Manages local educational institutions, educational standards, and support services for schools. It may also cover adult education and special educational needs (SEN). 

 

Environmental Services - Focuses on waste collection and disposal, recycling, street cleaning, environmental conservation, and management of public parks and green spaces. 

 

Housing - Provides social housing options, addresses homelessness issues, and enforces housing standards across the council area. 

 

Planning and Development - Manages land use, planning permissions for new developments, building control, and ensures that development meets local planning policy. 

 

Transport and Highways - Responsible for the maintenance and improvement of local roads, footpaths, and public rights of way. This department also oversees public transportation services and parking enforcement. 

 

Public Health - Works to improve the health and wellbeing of the local population, including health promotion initiatives, disease prevention, and emergency preparedness. 

 

Leisure and Culture - Manages libraries, museums, sports facilities, and cultural events, promoting community engagement and access to recreational activities. 

 

Finance and Resources - Manages the council's budget, financial planning, procurement, and ensures value for money in the delivery of services. 

 

Human Resources - Responsible for employee relations, recruitment, training and development, and ensuring compliance with employment laws. 

 

Legal Services - Provides legal advice to the council, manages legal disputes, and ensures that council actions comply with the law. 

 

Regulatory Services - Encompasses licensing (e.g., alcohol and taxi licenses), food safety, trading standards, and environmental health, ensuring businesses comply with regulations. 

 

Community Services - Works to engage with and support local communities, including managing community centers, supporting voluntary sector activities, and facilitating community development. 

 

Information Technology - Manages the council's IT infrastructure, digital services, and cybersecurity, ensuring the efficient operation and security of digital resources. 

 

Customer Services - The first point of contact for residents seeking information or services, managing inquiries through various channels (in-person, online, phone). 

 The request that needs to be redirected to one of the above departments is enclosed in double qoutes: "{text}"

 You only need to return the department name and nothing else.

'''
    response=client.chat.completions.create(
            model="gpt-4-1106",
            messages=[{"role":"system", "content":content}]
        )
    return response.choices[0].message.content



def extract_json_from_braces(text):
    # Find the first opening brace
    start_index = text.find('{')
    if start_index == -1:
        return None

    # Count braces to find the corresponding closing brace
    count = 1
    end_index = start_index + 1
    while end_index < len(text) and count > 0:
        if text[end_index] == '{':
            count += 1
        elif text[end_index] == '}':
            count -= 1
        end_index += 1

    # Extract the substring
    json_string = text[start_index:end_index]
    return json_string


async def process_request_item(request_item, cost, online_info):
    if cost is not None and cost.lower() == "yes":
        response = {
            "request": request_item,
            "response": "We refuse to answer this request  under Section 12 of FOIA as it exceeds statuary cost limit.",
            "exemptions": {}
        }
        return response
    # elif cost is None:
    #     docs = await vectorstore.asimilarity_search_with_score(request_item)
    #     print("*************")
    #     print(docs)
    #     extracted_documents = {}
    #     for doc, score in docs:
    #         page_content = doc.page_content
    #         redactions = doc.metadata.get('redactions', {})
    #         extracted_documents[page_content] = redactions
    #     response = await get_response(request_item, extracted_documents)
    #     print("response")
    #     print(response)
    #     response = extract_json_from_braces(response)
    #     response = ast.literal_eval(response)
    #     return response
    
    elif online_info.lower() == "yes":
        response = {
            "request": request_item,
            "response": "We found that the information you requested is already available and accessible on our official website. You can access the information directly through the following link: https://n-somerset.gov.uk/ .",
            "exemptions": {}
        }
        return response

    else:
        docs = await vectorstore.asimilarity_search_with_score(request_item)
        print("*************")
        print(docs)
        extracted_documents = {}
        for doc, score in docs:
            page_content = doc.page_content
            redactions = doc.metadata.get('redactions', {})
            extracted_documents[page_content] = redactions
        response = await get_response(request_item, extracted_documents)
        print("response")
        print(response)
        response = extract_json_from_braces(response)
        response = ast.literal_eval(response)
        
        return response
        
async def process_requests(request_list):
    print(len(request_list))
    print(type(request_list))
    tasks = [check_for_completeness(request) for request in request_list]
    print(len(tasks))
    results = await asyncio.gather(*tasks)
    complete_dict = dict(zip(request_list, results))
    return complete_dict

async def combine_json(validation_json, opinionated_json):
    combined_result = {}

    for key in validation_json.keys():
        combined_result[key] = {
            "validation": bool(validation_json[key].keys()),
            "validation_evidence":  next(iter(validation_json[key].values()))
        , 
            "opinionated": bool(opinionated_json[key].keys()),
            "opinionated_evidence": next(iter(opinionated_json[key].values()))
        }
    return combined_result


async def combine_function(request_list):
    validation_task = validation_of_request(request_list)
    opinionated_task = check_for_opinioted(request_list)
                # Await both tasks concurrently
    tasks=[validation_task, opinionated_task]
    validation_result, opinionated_result = await asyncio.gather(*tasks)
    opinionated_json = extract_json_from_braces(opinionated_result)
    opinionated_json = ast.literal_eval(opinionated_json)
    validation_json = extract_json_from_braces(validation_result)
    validation_json = ast.literal_eval(validation_json)

    print("printing the json")
    print(validation_json)
    print("printing the opinionated json")
    print(opinionated_json)
    combined_result = await combine_json(validation_json, opinionated_json)
    print("combined result")
    print(combined_result)
    return combined_result







################################ End Points Start here ################################ 




@application.route("/", methods=["GET"])
def login():
    return render_template('login.html')


@application.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        user = get_user_by_username(username)
        if user is None or not verify_password(password, user.hashed_password):
            logging.debug("Invalid username or password")
            return 'hello' # Redirect to dashboard page after successful login
        else:
            session["user"]=username
            return redirect(url_for("index"))
    return render_template("login.html")
    

@application.route("/index", methods=["GET", "POST"])
async def index():
    if 'user' in session:
        if request.method == "POST":
            session.pop("foi_request", None)
            session.pop("valid_dict", None)
            session.pop("vexatious_flag", None)
            session.pop("repeated_dict", None)
            session.pop("request_list", None)
            session.pop("valid_flag", None)


            input_text = request.form.get("input_text")
            
            email_id = request.form.get("email_id")
            timestamp = request.form.get("timestamp")
            logging.debug("Received input_text: %s, email_id: %s, timestamp: %s", input_text, email_id, timestamp)
            full_name=await check_full_name(input_text)
            logging.debug("Full name: %s", full_name)
            print("fuction")
            
            print(full_name)
            print(type(full_name))
            return jsonify({'full_name': full_name})
        else:
            return render_template('index.html')
    else:
        logging.debug("User is not logged in")
        return '''
        <script>
            alert('Sorry but you need to login to work with FOI');
            window.location = '/login';  
        </script>
        '''
    


@application.route("/full_name", methods=["GET","POST"])
async def full_name():
     if 'user' in session:
        if request.method == "POST":
            input_text = request.form.get("input_text")
            
            email_id = request.form.get("email_id")
            timestamp = request.form.get("timestamp")
            session["foi_request"]=input_text
            session["email_id"]=email_id
            session["timestamp"]=timestamp
            full_name_task = check_full_name(input_text)
            vexatious_task = check_for_vexatious(input_text)
            tasks=[full_name_task, vexatious_task]
            full_name_result, vexatious_result = await asyncio.gather(*tasks)
            vexatious_dict = parse_terminal_dict(vexatious_result)
            vexatious_dict = ast.literal_eval(vexatious_dict)
            print("vextious dict")
            print(vexatious_dict)
            print(type(vexatious_dict))
            vexatious, vex_evidence = next(iter(vexatious_dict.items()))
            print(vex_evidence)
            full_name_result = ast.literal_eval( full_name_result)
            print(full_name_result)
            print(type(full_name_result))
            if vexatious==False:
                refusal=await refusal_notice("It is a vexatious request", input_text)
                return render_template('triage.html', foi_request=input_text, full_name=full_name_result, vexatious=vexatious, vex_evidence=vex_evidence, refusal=refusal)
            else:
                return render_template('triage.html', foi_request=input_text, full_name=full_name_result, vexatious=vexatious, vex_evidence=vex_evidence)
        else:
            return render_template('index.html')
     else:
        logging.debug("User is not logged in")
        return '''
        <script>
            alert('Sorry but you need to login to work with FOI');
            window.location = '/login';  
        </script>
        '''


@application.route("/identify_parts", methods=["GET","POST"])
async def identify_parts():
     result_list=None
     if 'user' in session:
            logging.debug("User is logged in")
            foi_request=session.get("foi_request")
            logging.debug("FOI Request: %s", foi_request)
            result_list =await identify_part(foi_request)
            logging.debug("Result List: %s", result_list)
            result_list=safe_literal_eval(result_list)
            session["request_list"]=result_list
            return render_template("identify_parts.html", result=result_list)
            
     else:
         logging.debug("User is not logged in")
         return '''
        <script>
            alert('Sorry but you need to login to work with FOI');
            window.location = '/login';  
        </script>
        '''
        
        

@application.route("/validate", methods=["GET","POST"])
async def validation():
    if 'user' in session:
            if request.method == "POST":
                request_list = session.get("request_list")
                combined_result=await combine_function(request_list)
                session["validation_json"] = combined_result
                return render_template("validation.html", validation_json=combined_result)
            else:
                validation_json = session.get("validation_json", {})
                return render_template("validation.html", validation_json=validation_json)
    else:
        return '''
            <script>
                alert('Sorry but you need to login to work with FOI');
                window.location = '/login';  
            </script>
            '''


@application.route("/check_repeated", methods=["GET","POST"])
async def check_repeated():
    if 'user' in session:
        if request.method == "POST":
            email = session.get("email_id")
            latest_timestamp = session.get("timestamp")
            validation_json = session.get("validation_json")
            valid_flag = {}

            for component, status_dict in validation_json.items():
                if 'True' in status_dict and status_dict['True']:
                    valid_flag[component] = 1
                else:
                    valid_flag[component] = 0
            session["valid_flag"] = valid_flag

            previous_requests = get_requests_by_email(email, latest_timestamp)
            gpt_repeated_dict = await check_for_repeated(previous_requests, session.get("request_list"))
            repeated_dict = parse_terminal_dict(gpt_repeated_dict)
            repeated_dict = ast.literal_eval(repeated_dict)
            session.pop("timestamp", None)
            session.pop("email_id", None)
            repeated_flag = {key: 0 if value.startswith("Repeated") else 1 for key, value in repeated_dict.items()}
            session["repeated_dict"] = repeated_dict

            return render_template("check_repeated.html", repeated_dict=repeated_dict, valid_flag=valid_flag)
        else:
            return render_template("check_repeated.html", repeated_dict=session.get("repeated_dict"), valid_flag=session.get("valid_flag"))
    else:
        return '''
        <script>
            alert('Sorry but you need to login to work with FOI');
            window.location = '/login';  
        </script>
        '''



@application.route("/completeness_check", methods=["GET","POST"])
async def check_completeness():
    

    
    services = [
            "Adult Services",
            "Children's Services",
            "Education Services",
            "Environmental Services",
            "Housing",
            "Planning and Development",
            "Transport and Highways",
            "Public Health",
            "Leisure and Culture",
            "Finance and Resources",
            "Human Resources",
            "Legal Services",
            "Regulatory Services",
            "Community Services",
            "Information Technology",
            "Customer Services"
        ]
    if request.method =="POST":
        department_list=[]
        online_info_present_json = request.form.get('online_info_answers')
        print(online_info_present_json)
        
        request_list=session.get("request_list")
        print("checking completeness endpoint")
        print(request_list)
        
        online_info_present = json.loads(online_info_present_json) if online_info_present_json else []
        print("checking the json now")
        print(online_info_present)
        request_list_filtered = [request_list[i] for i, val in enumerate(online_info_present) if val.lower() != "yes"]
        session["online_info_present"]=online_info_present
        complete_dict = await process_requests(request_list_filtered)

        department_list = []
        for key, value in complete_dict.items():
            if isinstance(value, str) and '%' in value:
                percentage_value = float(value.replace('%', ''))
                if percentage_value < 100:
                    department = await get_department(key)
                    department_list.append(department)
                else:
                    department_list.append("-")
            else:
                print(f"The value for key '{key}' is not in string format or doesn't contain '%' sign.")
        print(complete_dict)
        session["complete_dict"] = complete_dict
        session["department_list"] = department_list
        all_values_100_percent = all(value == "100%" for value in complete_dict.values())
        session["all_values_100_percent"] = all_values_100_percent
        print("department_list")
        print(department_list)

        return render_template("completeness.html", complete_dict=complete_dict, department_list=department_list, services=services, all_values_100_percent=all_values_100_percent)
    else:
        return render_template("completeness.html", complete_dict=session.get("complete_dict"), department_list=session.get("department_list"), services=services, all_values_100_percent=session.get("all_values_100_percent"))

@application.route("/retrieval", methods=["GET", "POST"])
async def retrieval():
    if 'user' in session:
        if request.method == "POST":
            session.pop("complete_dict", None)
            session.pop("department_list", None)
            session.pop("timestamp", None)
            session.pop("email_id", None)
            session.pop("all_values_100_percent", None)
            
            # Check if the 'cost_limit' field is present in the form
            selected_values_json = request.form.get('cost_limit')
            cost_limit_values = json.loads(selected_values_json) if selected_values_json else []
            session["cost_limit"] = cost_limit_values
            
            # Check if the 'council_info' field is present in the form
            selected_values_2 = request.form.get('council_info')
            council_info = json.loads(selected_values_2) if selected_values_2 else []
            
            return render_template("retrieval.html")
        else:
            request_list = session.get("request_list")
            cost_limit = session.get("cost_limit")
            
            responses = []
            tasks = []
            online_info_present=session.get("online_info_present")
            
            if cost_limit:  # Check if cost_limit is not None and not empty
                for request_item, cost_limit_value, online_info_value in zip(request_list, cost_limit, online_info_present):
                    tasks.append(process_request_item(request_item, cost_limit_value, online_info_value))
            else:
                # If cost_limit is empty or None, iterate over request_list without zipping
                for request_item, online_info_value in zip(request_list, online_info_present):
                    tasks.append(process_request_item(request_item, None, online_info_value))
            
            responses = await asyncio.gather(*tasks)
            
            response_dict = responses
            
            # Pop the 'cost_limit' from session after use
            session.pop("cost_limit", None)
            
            return jsonify(response_dict)
    else:
        return '''
        <script>
            alert('Sorry but you need to login to work with FOI');
            window.location = '/login';  
        </script>
        '''



@application.route("/response", methods=["GET","POST"])
async def response():
    global final

    if 'user' in session:
        if request.method =="POST":
            try:
                request_list=session.get("request_list")
                data=request.get_json()

                extracted_data = [{'question': item['question'], 'answer': item['answer']} for item in data['data']]
                
                final=extracted_data
                logging.debug("Extracted data: %s", extracted_data)
                
                return jsonify({"message":"successss"})
            except Exception as e:
                logging.error("An error occurred in /response POST endpoint: %s", e)
                return "An error occurred, please try again later."
        else:
            try:
                foi_request=session.get("foi_request")
                valid_dict = session.get("valid_dict")
                vex_dict = session.get("vexatious_flag")
                repeated_dict = session.get("repeated_dict")
                request_list=session.get("request_list")

                acknowledgment_text = await generate_acknowledgement_letter(request_list, valid_dict, vex_dict, repeated_dict)
                logging.debug("Acknowledgment text: %s", acknowledgment_text)
                
                response_letter=await generate_response_letter(foi_request, final)
                logging.debug("Response letter: %s", response_letter)

                return render_template("response.html", response=response_letter, acknowledgment_text=acknowledgment_text)
            except Exception as e:
                logging.error("An error occurred in /response GET endpoint: %s", e)
                return "An error occurred, please try again later."
    else:
         return '''
        <script>
            alert('Sorry but you need to login to work with FOI');
            window.location = '/login';  
        </script>
        '''



@application.route("/refusal", methods=["POST", "GET"])
def refusal():
    if request.method == "POST":
        try:
            refusal_text = request.form.get("refusal_text")
            print("refusal endpoimt")
            print(refusal_text)
            return render_template("refusal.html", refusal_text=refusal_text)
        except Exception as e:
            logging.error("An error occurred in /refusal POST endpoint: %s", e)
            return "An error occurred, please try again later."
    else:
        return "Method not allowed"


@application.route("/save_document", methods=["POST"])
def save_document():
    try:
        document_data = request.json
        text = document_data.get("text")
        filename = document_data.get("filename")

        # Create a new Document
        doc = Document()
        doc.add_paragraph(text)

        # Save the document to a BytesIO object
        doc_buffer = BytesIO()
        doc.save(doc_buffer)
        doc_buffer.seek(0)

        # Return the document as a FileResponse for download
        return send_file(
            doc_buffer,
            mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            as_attachment=True  # This automatically sets the filename for download
        )
    except Exception as e:
        logging.error("An error occurred in /save_document endpoint: %s", e)
        return "An error occurred, please try again later."


@application.route("/logout")
async def logout():
    try:
        if 'user' in session:
            user = session.pop('user')
            logging.info("User '%s' has logged out.", user)
        
        return redirect(url_for('login'))
    except Exception as e:
        logging.error("An error occurred in /logout endpoint: %s", e)
        return "An error occurred, please try again later."




    