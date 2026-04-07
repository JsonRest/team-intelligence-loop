FROM python:3.11-slim

RUN pip install uv --no-cache-dir

# workspace-mcp is the correct PyPI package (not google-workspace-mcp)
# It installs the `workspace-mcp` CLI binary and reads GOOGLE_OAUTH_* env vars
RUN uv pip install --system workspace-mcp --no-cache-dir

ENV PORT=8080
EXPOSE 8080

CMD ["workspace-mcp", "--tools", "gmail", "calendar", "--transport", "streamable-http"]
