import os, json
os.environ['DAILY_BRIEFING_DATA']='/tmp/health_test'
from app.web import create_app
app=create_app()
client=app.test_client()
resp=client.get('/healthz')
print('status', resp.status_code)
print('data', resp.get_data(as_text=True))
