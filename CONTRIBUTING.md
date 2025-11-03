CONTRIBUTING.md
Contributing Guide

This document explains how our team contributes to the LLM-Powered Conversational Home Assistant project. It ensures that all work follows the team’s agreed-upon processes, meets the Definition of Done, and supports transparency and collaboration.

Workflow

– Everyone works on the main branch by default.
– Large or experimental features may use a short-lived branch named feature/<name>.
– Before pushing, confirm that all code runs correctly and follows the team’s Definition of Done.
– Each push to GitHub automatically posts an update in Microsoft Teams so the team can see progress.
– All tasks are tracked in Trello. When a task is completed, the Trello card is moved from Doing → Done, and the member posts a short completion note in the Teams “Completed Tasks” channel with a reference to the commit or card.
– Frequent, small commits are encouraged to maintain project visibility and prevent merge conflicts.

Definition of Done (DoD)

A task or feature is considered complete when:
– The code runs without errors and functions as intended.
– Documentation such as the README, changelog, or API files is updated if behavior changes.
– At least one teammate reviews large or shared features.
– The CI/CD workflow in .github/workflows/ci.yml completes successfully.
– The Trello card for the task is moved to Done.
– A brief message is posted in Microsoft Teams announcing completion.
– GitHub automatically notifies the Teams channel of the latest push or merge.

Reviews and Merges

– Small commits may be pushed directly to main.
– Large or shared features require one approving review before merging.
– Reviewers verify that the change meets the Definition of Done, includes updated documentation, and does not break existing functionality.
– All reviews and approvals are tracked through GitHub pull requests.

Documentation and Decisions

– All major design or technical decisions are recorded in DECISIONS.md.
– Documentation (README, API references, changelog) must be updated whenever new features are added or modified.
– Every member is responsible for keeping their assigned sections current and accurate.

Communication and Support

– The main communication platform is Microsoft Teams.
– Quick coordination can occur through SMS, but important updates should always be shared in Teams.
– Mentor and TA meetings are held biweekly on Zoom, coordinated by Jonathan Guzman.
– Questions, updates, and requests for help must be posted in Teams so all members stay informed.
– All discussions, updates, and meeting notes are stored in /docs/meetings/.

Accountability

– Every change to the project is verifiable through GitHub commits, Trello updates, or Teams announcements.
– Members are expected to communicate progress regularly, attend meetings, and meet deadlines.
– Evidence of contribution includes pull requests, commits, and activity records across GitHub, Teams, and Trello.

