# Contributing to LLM Home Assistant

Thank you for your interest in contributing to LLM Home Assistant!

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/your-username/llm-home-assistant.git
   cd llm-home-assistant
   ```
3. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Workflow

### Code Standards

- Follow [PEP 8](https://pep8.org/) style guidelines for Python code
- Use type hints where appropriate
- Write docstrings for public functions and classes
- Keep functions focused and small (prefer single responsibility)

### Testing

- Run existing tests before making changes:
  ```bash
  python3 -m pytest tests/
  ```
- Add tests for new functionality
- Ensure all tests pass before submitting a pull request

### Commit Guidelines

- Use clear, descriptive commit messages
- Keep commits focused and atomic (one logical change per commit)
- Reference issue numbers where applicable

## Pull Request Process

1. Update documentation for any changed behavior
2. Add or update tests as needed
3. Ensure the CI/CD pipeline passes
4. Request review from a maintainer
5. Your PR will be reviewed and merged once approved

## Definition of Done

A contribution is considered complete when:

- Code runs without errors and functions as intended
- Documentation is updated if behavior changes
- Tests are added or updated for new functionality
- All CI checks pass

## Reporting Issues

- Use [GitHub Issues](https://github.com/your-repo/llm-home-assistant/issues) to report bugs or request features
- Include your Home Assistant version, Python version, and relevant logs
- For bugs, provide steps to reproduce the issue

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
