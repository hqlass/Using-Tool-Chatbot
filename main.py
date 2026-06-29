import ssl
import certifi
import os
from dotenv import load_dotenv
import subprocess
import sys

# Auto silent installer for dependencies
def install(package):
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", package, "-q"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def ensure_dependencies():
    required = [
        "groq",
        "python-dotenv",
        "certifi",
        "tavily-python"
    ]

    for package in required:
        try:
            __import__(package.replace("-", "_"))
        except ImportError:
            install(package)

ensure_dependencies()


from groq import Groq
from tavily import TavilyClient

ssl._create_default_https_context = ssl._create_unverified_context

# ENV setup
if not os.path.exists(".env"):
    groq_key = input("Enter your Groq API key: (If you don't have a Groq API key, you can get one here: https://groq.com)")
    tavily_key = input("Enter your Tavily API key: (If you don't have a Tavily API key, you can get one here: https://tavily.com)")
    with open(".env", "w") as f:
        f.write(f"GROQ_API_KEY={groq_key}\n")
        f.write(f"TAVILY_API_KEY={tavily_key}\n")

load_dotenv()

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))

query = input("Enter your search query: ")
response = tavily_client.search(query=query, max_results=5)

context = ""

for result in response["results"]:
    context += f"""
Title: {result['title']}
Content: {result['content']}
URL: {result['url']}
"""

chat_history = []

while True:
    user_input = input("If there is anything you would like to add (or type 'exit' to finish): ")

    if user_input.lower() in ["exit", "finish"]:
        print("Conversation ended. Have a nice day!")
        break

    prompt = f"""
You are a helpful AI assistant.

Use ONLY the web results below:

{context}

User question:
{user_input}
"""

    chat_history.append({"role": "user", "content": user_input})

    response = groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {"role": "user", "content": prompt}
        ],
    )

    answer = response.choices[0].message.content
    print("\n", answer)