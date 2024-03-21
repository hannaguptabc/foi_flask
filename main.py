 
from application import application
import asyncio
from controller import *


async def main():
    await application.run(debug=True, port=8000)
    
if __name__ == '__main__':
    asyncio.run(main())
    
