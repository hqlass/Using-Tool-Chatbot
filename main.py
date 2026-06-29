"""
Unified Agent
─────────────────────────────────────────
features:
  • Calculator (AST , safe)
  • Weather     (WeatherAPI)
  • Web Search       (Tavily)
  • OCR / Image    (EasyOCR + OpenCV)
  • PDF / Document   (pdfplumber, metin + tablo)
  • Chat History  
  • System prompt
  • Prompt Injection guards 
"""

#dependency installer
import subprocess, sys

def _install(pkg):
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", pkg, "-q"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

def ensure_dependencies():
    packages = {
        "groq": "groq",
        "dotenv": "python-dotenv",
        "requests": "requests",
        "certifi": "certifi",
        "tavily": "tavily-python",
        "cv2": "opencv-python",
        "easyocr": "easyocr",
        "numpy": "numpy",
        "pdfplumber": "pdfplumber",
    }
    for module, pip_name in packages.items():
        try:
            __import__(module)
        except ImportError:
            print(f"[+] Kuruluyor: {pip_name}")
            _install(pip_name)

ensure_dependencies()

#imports
import ast, operator, os, ssl, json
from groq import BadRequestError
import numpy as np
import cv2
import requests
import pdfplumber
import easyocr
from dotenv import load_dotenv
from groq import Groq
from tavily import TavilyClient

ssl._create_default_https_context = ssl._create_unverified_context

# .env installation
if not os.path.exists(".env"):
    print("=== İlk kurulum: API anahtarları ===")
    groq_key    = input("Groq API anahtarı    : ").strip()
    weather_key = input("WeatherAPI anahtarı  : ").strip()
    tavily_key  = input("Tavily API anahtarı  : ").strip()
    with open(".env", "w") as f:
        f.write(f"GROQ_API_KEY={groq_key}\n")
        f.write(f"WEATHER_API_KEY={weather_key}\n")
        f.write(f"TAVILY_API_KEY={tavily_key}\n")
    print()

load_dotenv()

groq_client    = Groq(api_key=os.getenv("GROQ_API_KEY"))
tavily_client  = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
weather_api_key = os.getenv("WEATHER_API_KEY")

MAX_HISTORY = 20   # Maximum number of previous messages to keep in context (excluding system prompt)


#tools

#Calculator
OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.FloorDiv: operator.floordiv,
}

def _validate(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.Name, ast.Attribute, ast.Call)):
            raise ValueError(f"insecure expression: {type(child).__name__}")
        _validate(child)

def _eval(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand)
        return +v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        op_type = type(node.op)
        if op_type not in OPS:
            raise ValueError(f"Unknown operator: {op_type}")
        if op_type in (ast.Div, ast.FloorDiv) and right == 0:
            raise ValueError("Division by zero error")
        return OPS[op_type](left, right)
    raise ValueError(f"Unsupported node: {type(node).__name__}")

def calculator_tool(expression: str) -> str:
    try:
        tree = ast.parse(expression, mode="eval")
        _validate(tree.body)
        return str(_eval(tree.body))
    except (ValueError, SyntaxError) as e:
        return f"Calculation error: {e}"


#Weather
def get_weather(city: str) -> str:
    try:
        resp = requests.get(
            "https://api.weatherapi.com/v1/current.json",
            params={"key": weather_api_key, "q": city, "lang": "tr"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            return f"Weather error: {data['error']['message']}"
        loc = data["location"]["name"]
        cur = data["current"]
        return (
            f"City     : {loc}\n"
            f"Temperature: {cur['temp_c']} °C\n"
            f"Condition: {cur['condition']['text']}\n"
            f"Humidity : {cur['humidity']}%\n"
            f"Wind     : {cur['wind_kph']} km/h"
        )
    except requests.exceptions.Timeout:
        return "Error: The request timed out."
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"


#Web search (Tavily)
def web_search_tool(query: str) -> str:
    try:
        response = tavily_client.search(query=query, max_results=5)
        if not response.get("results"):
            return "Error: No search results found."
        parts = []
        for r in response["results"]:
            parts.append(
                f"Title  : {r['title']}\n"
                f"Summary: {r['content'][:300]}\n"
                f"URL    : {r['url']}"
            )
        raw = "\n\n".join(parts)
        return sanitize_tool_output(raw, "web_search")
    except Exception as e:
        return f"Web search error: {e}"




INJECTION_PATTERNS = [
    "ignore previous", "ignore all", "disregard",
    "system prompt", "new instruction", "act as",
    "you are now", "forget your", "override",
    "\\n\\n#", "</s>", "<|im_end|>",           # model control tokens
]

def sanitize_tool_output(text: str, source: str = "tool") -> str:
    lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern.lower() in lower:
            print(f"[SECURITY ALERT] Suspicious pattern found in '{source}' output: '{pattern}'")
            return (
                f"[SECURITY] This {source} output was blocked due to suspicious content. "
                f"Suspicious pattern detected: '{pattern}'"
            )
    return text


#PDF Read (pdfplumber)
_doc_context: str = ""   

def read_document_tool() -> str:
    global _doc_context
    try:
        import tkinter as tk
        from tkinter.filedialog import askopenfilename
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        path = askopenfilename(
            title="Select document",
            filetypes=[
                ("PDF files", "*.pdf"),
                ("Text files", "*.txt *.md *.csv"),
                ("All files",   "*.*"),
            ],
        )
        root.destroy()

        if not path:
            return "No file selected, operation cancelled."

        print(f"[DOCUMENT] Selected file: {path}")
        ext = os.path.splitext(path)[1].lower()

        #PDF
        if ext == ".pdf":
            pages_text = []
            with pdfplumber.open(path) as pdf:
                for i, page in enumerate(pdf.pages, 1):
                    text = page.extract_text() or ""

                    
                    tables = page.extract_tables()
                    table_str = ""
                    for table in tables:
                        for row in table:
                            
                            table_str += " | ".join(
                                cell if cell else "" for cell in row
                            ) + "\n"

                    combined = (text + "\n" + table_str).strip()
                    if combined:
                        pages_text.append(f"[Page {i}]\n{combined}")

            if not pages_text:
                return (
                    "PDF text could not be extracted. "
                    "The PDF might be scanned (image-based) — "
                    "try the 'read image' (OCR) tool instead."
                )

            full_text = "\n\n".join(pages_text)
            truncated = full_text[:4000]
            if len(full_text) > 4000:
                truncated += f"\n\n[... {len(full_text)-4000} characters truncated]"

            _doc_context = truncated
            return f"PDF read ({len(pages_text)} pages):\n\n{sanitize_tool_output(truncated, 'PDF')}"

        
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read()
            truncated = raw[:4000]
            if len(raw) > 4000:
                truncated += f"\n\n[... {len(raw)-4000} characters truncated]"
            _doc_context = truncated
            return f"File read:{sanitize_tool_output(truncated, 'file')}"

    except Exception as e:
        return f"File reading error: {e}"

#OCR (EasyOCR)
_ocr_reader = None          
_ocr_context: str = ""      

def _get_ocr_reader():
    global _ocr_reader
    if _ocr_reader is None:
        print("[OCR] Loading model, please wait...")
        _ocr_reader = easyocr.Reader(["tr", "en"], gpu=False)
    return _ocr_reader

def ocr_tool() -> str:
    global _ocr_context
    try:
        import tkinter as tk
        from tkinter.filedialog import askopenfilename
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)   

        image_path = askopenfilename(
            title="Image Selection",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.bmp *.tiff *.webp")],
        )
        root.destroy()

        if not image_path:
            return "No image selected, operation cancelled."

        print(f"[OCR] Selected file: {image_path}")

        data = np.fromfile(image_path, dtype=np.uint8)
        img  = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if img is None:
            return "Image could not be loaded, file might be corrupted."

        reader  = _get_ocr_reader()
        results = reader.readtext(img)
        text    = " ".join([r[1] for r in results]).strip()

        if not text:
            return "No readable text found in the image."

        _ocr_context = text
        return f"Text read from image:\n{text}"
    except Exception as e:
        return f"OCR error: {e}"


# TOOl schema definitions for LLM tool calling  

tools = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "Safe math calculator. Supports + - * / ** % // and unary operators.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Example: 2+3*4 or -(5+2)"}
                },
                "required": ["expression"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Gets the current weather conditions for a city. "
                "Use when the user asks about weather, temperature, rain, humidity or wind."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "City name. Example: Ankara, İstanbul, Tokyo"}
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Searches for current information on the internet. Use for news, recent events, "
                "and questions requiring up-to-date information."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ocr_read",
            "description": (
                "Reads text from an image file (OCR). File selector opens automatically. "
                "Use when the user wants to read text from an image or photo. "
                "Do not send parameters, just call the tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pdf_read",
            "description": (
                "Reads PDF, TXT, CSV or MD files. File selector opens automatically. "
                "Use when the user wants to read a document, file or PDF. "
                "Do not send parameters, just call the tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]


# TOOL Runner

def run_tool(name: str, args: dict) -> str:
    if name == "calculator":
        return calculator_tool(args["expression"])
    if name == "get_weather":
        return get_weather(args["city"])
    if name == "web_search":
        return web_search_tool(args["query"])
    if name == "ocr_read":
        return ocr_tool()
    if name == "pdf_read":
        return read_document_tool()
    return f"Unknown tool: {name}"



# SYSTEM PROMPT

SYSTEM_PROMPT = """You are a skilled Turkish and English assistant. You have the following tools at your disposal.:

• calculator  → math operations
• get_weather → current weather information
• web_search  → search for up-to-date information on the internet
• ocr_read    → read text from an image file (OCR)
• pdf_read    → read PDF, TXT, CSV, MD files

When the user wants to read text from an image, use ocr_read. When the user wants to read a document or PDF, use pdf_read.
Use web_search when up-to-date information is needed.
Please provide short and clear answers in Turkish or English."""




def build_user_message(user_input: str) -> str:
    parts = [user_input]
    if _ocr_context:
        parts.append(f"[Context — OCR text]:\n{_ocr_context}")
    if _doc_context:
        parts.append(f"[Context — Document text]:\n{_doc_context}")
    return "\n\n".join(parts)


# ══════════════════════════════════════════════════════════
# ANA DÖNGÜ
# ══════════════════════════════════════════════════════════
messages = [{"role": "system", "content": SYSTEM_PROMPT}]

print("=" * 55)
print("Unified Agent | To exit: quit / exit / q")
print("  Tools: Calculator · Weather · Web Search · OCR")
print("=" * 55)
print()

while True:
    try:
        user_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSee you later!")
        break

    if not user_input:
        continue

    if user_input.lower() in ("quit", "exit", "çıkış", "q"):
        print("See you later!")
        break

    # For token limit management, keep only the last MAX_HISTORY messages + system prompt
    if len(messages) > MAX_HISTORY + 1:
        messages = [messages[0]] + messages[-MAX_HISTORY:]

    messages.append({"role": "user", "content": build_user_message(user_input)})

    MODEL = "llama-3.3-70b-versatile"

    try:
        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
    except BadRequestError as e:
        print(f"[Warning] Tool call failed, falling back to regular response... ({e})")
        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=messages,
        )
        reply = response.choices[0].message.content
        messages.append({"role": "assistant", "content": reply})
        print(f"\nAsistan: {reply}\n")
        continue

    msg = response.choices[0].message

    #Is there any tool usage involved?
    if msg.tool_calls:
        messages.append(msg)

        for tool_call in msg.tool_calls:
            name   = tool_call.function.name
            args   = json.loads(tool_call.function.arguments)
            result = run_tool(name, args)

            print(f"[{name}] called.")

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        #Main response after tool usage
        try:
            final = groq_client.chat.completions.create(
                model=MODEL,
                messages=messages,
            )
            reply = final.choices[0].message.content
        except BadRequestError:
            reply = "An error occurred while generating the response, please try again."

        messages.append({"role": "assistant", "content": reply})
        print(f"\n assistant: {reply}\n")

    else:
        reply = msg.content
        messages.append({"role": "assistant", "content": reply})
        print(f"\n assistant: {reply}\n")
