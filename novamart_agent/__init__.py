from pathlib import Path
from dotenv import load_dotenv

# Carga el archivo .env ubicado en la raíz del proyecto (novamart-ops-agent/.env)
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

from . import agent