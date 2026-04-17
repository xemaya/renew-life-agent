# Extends the platform-provided Python base image — FastAPI / uvicorn /
# httpx / anthropic / a2h-agent / a2h-cli are already installed there.
FROM a2h/agent-base:python-3.12-http

COPY --chown=agent:agent . /opt/agent
WORKDIR /opt/agent

# renew-life-agent doesn't need anything beyond what the base image ships,
# but keep the install step so sellers see the right pattern.
RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; fi

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8080"]
