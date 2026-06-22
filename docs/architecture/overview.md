# Support Agent Architecture Overview

This repository follows an application-first baseline:
- FastAPI application as the core system
- PostgreSQL as system of record
- direct LLM path by default
- Hermes as optional advanced backend
- external support knowledge layer outside the application repository
