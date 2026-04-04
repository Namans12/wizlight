# skills/wiz_config/fetch_docs.py

import requests
from bs4 import BeautifulSoup

def get_wiz_docs():
    url = "https://docs.pro.wizconnected.com/"
    res = requests.get(url)

    soup = BeautifulSoup(res.text, "html.parser")

    steps = []
    for h in soup.find_all("h2"):
        steps.append(h.text)

    return steps

if __name__ == "__main__":
    print(get_wiz_docs())