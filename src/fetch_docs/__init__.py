import asyncio
from .fetch import download

BASE_URL = "https://learn.omacom.io/2/the-omarchy-manual"
DOCS_DIR = "/mnt/aidata/datasets/documents/omarchy/markdown"

def main():
    asyncio.run(
        download(BASE_URL, DOCS_DIR)
    )

if __name__ == "__main__":
    main()

