import os
from google.adk.agents import Agent

MODEL_NAME = os.environ["GEMINI_MODEL"]

root_agent = Agent(
    name="novamart_support_agent",
    model=MODEL_NAME,
    description="Internal AI soport assistant for Novamart store employees.",
    instruction=(
        "You are the internal support assistant for Novamart store employees. "
        "Answer clearly and concisely."
    ),
)
