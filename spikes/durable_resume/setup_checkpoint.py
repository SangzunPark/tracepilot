import os

from langgraph.checkpoint.postgres import PostgresSaver

database_url = os.environ["DATABASE_URL"]

with PostgresSaver.from_conn_string(database_url) as checkpointer:
    checkpointer.setup()

print("PostgreSQL checkpointer setup complete.")