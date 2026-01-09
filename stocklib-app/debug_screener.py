import requests
import random

USER_AGENTS = ["Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.0.0 Safari/537.36"]

def get_webpage_content(stock_name):
    url = f"https://www.screener.in/company/{stock_name}/consolidated/"
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        print(f"Fetching {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.text
    except Exception as e:
        return f"Error: {str(e)}"

if __name__ == "__main__":
    html = get_webpage_content("RELIANCE")
    if html.startswith("Error"):
        print(html)
    else:
        with open("debug_screener.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("Saved HTML to debug_screener.html")
