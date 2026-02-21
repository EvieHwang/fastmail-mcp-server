# fastmail-mcp-server

A remote MCP (Model Context Protocol) server that provides email read and search access to a Fastmail account via JMAP. Designed to run on a Mac mini and be accessible from the Claude iOS app through Tailscale Funnel.

## Status

🚧 Under development

## Key Features (V1)

- Email read and search via JMAP
- Streamable HTTP transport (MCP spec 2025-06-18)
- OAuth 2.1 authentication via AWS Cognito
- Runs as a persistent macOS launchd service
- Internet-accessible via Tailscale Funnel

## Documentation

- [Project Plan](docs/project-plan.md) — Architecture, technology choices, and implementation sequence
