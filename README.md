# AI on AKS (`aiks`)

A practical showcase of running AI workloads on Azure Kubernetes Service (AKS), covering both model inference and agent-based applications.

> [!NOTE]
> This project is under active development. The initial repository establishes the foundation; runnable examples and deployment guides will be added incrementally.

## Overview

`aiks` explores how AKS can provide a scalable, secure, and observable platform for AI workloads. The project will include reference implementations, infrastructure definitions, deployment manifests, and operational guidance for scenarios such as:

- Hosting open-source models for real-time inference
- Serving models with GPU acceleration
- Scaling inference workloads based on demand
- Running AI agents and multi-agent applications
- Connecting agents to models, tools, and data
- Securing workloads with Azure identity and Kubernetes controls
- Observing model and agent behavior in production
- Evaluating performance, reliability, and cost

The goal is to make each capability understandable and reproducible rather than present a single monolithic application.

## Planned Capabilities

### Model Inference

- CPU and GPU inference on AKS
- Model serving with production-ready inference runtimes
- Autoscaling and load testing
- Model storage and retrieval
- Traffic management and health checks
- Performance and cost comparisons

### AI Agents

- Containerized agent applications
- Single-agent and multi-agent examples
- Tool and data-source integration
- Agent-to-model communication
- Identity, secrets, and access control
- Tracing, evaluation, and observability

### Platform Engineering

- Reproducible Azure and AKS infrastructure
- Kubernetes deployment patterns
- Workload identity
- Network and security configuration
- Monitoring and diagnostics
- CI/CD and deployment automation

## Project Structure

The repository will evolve toward the following structure:

```text
aiks/
|-- docs/             # Architecture, concepts, and operational guides
|-- infrastructure/   # Azure and AKS infrastructure definitions
|-- platform/         # Shared Kubernetes platform components
|-- inference/        # Model-serving examples
|-- agents/           # Agent application examples
|-- samples/          # End-to-end showcase scenarios
|-- scripts/          # Setup, validation, and utility scripts
|-- tests/            # Automated validation and integration tests
|-- .gitignore
|-- LICENSE
`-- README.md
```

Folders will be introduced as working examples are added.

## Getting Started

Setup instructions will be added with the first runnable scenario. Examples will document their prerequisites, deployment steps, validation commands, and cleanup procedures.

Expected prerequisites include:

- An Azure subscription
- Azure CLI
- `kubectl`
- Docker or another compatible container build tool
- Access to an AKS cluster
- GPU quota for GPU-based scenarios

## Roadmap

The project will initially focus on:

1. Establishing a reproducible AKS foundation
2. Deploying a first model-inference workload
3. Adding GPU-backed model serving and autoscaling
4. Deploying a tool-using AI agent
5. Adding identity, observability, and evaluation
6. Building complete inference and agent showcase scenarios

The roadmap will evolve as implementations and findings are added.

## Contributing

Contributions, ideas, and feedback are welcome. Contribution guidelines will be added as the project develops.

## License

This project is licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.
