# Orbit

Orbit turns project work into isolated, autonomous implementation runs, allowing teams to manage
work instead of supervising coding agents.

[![Orbit demo video preview](.github/media/orbit-demo-poster.jpg)](.github/media/orbit-demo.mp4)

_In this [demo video](.github/media/orbit-demo.mp4), Orbit monitors a Linear board for work and spawns agents to handle the tasks. The agents complete the tasks and provide proof of work: CI status, PR review feedback, complexity analysis, and walkthrough videos. When accepted, the agents land the PR safely. Engineers do not need to supervise Codex; they can manage the work at a higher level._

> [!WARNING]
> Orbit is a low-key engineering preview for testing in trusted environments.

## Running Orbit

### Requirements

Orbit works best in codebases that have adopted
[harness engineering](https://openai.com/index/harness-engineering/). Orbit is the next step --
moving from managing coding agents to managing work that needs to get done.

### Option 1. Make your own

Tell your favorite coding agent to build Orbit in a programming language of your choice:

> Implement Orbit according to the following spec:
> https://github.com/TheRealOP/orbits/blob/main/SPEC.md

### Option 2. Use our experimental reference implementation

Check out [elixir/README.md](elixir/README.md) for instructions on how to set up your environment
and run the Elixir-based Orbit implementation. You can also ask your favorite coding agent to
help with the setup:

> Set up Orbit for my repository based on
> https://github.com/TheRealOP/orbits/blob/main/elixir/README.md

---

## License

This project is licensed under the [Apache License 2.0](LICENSE).
