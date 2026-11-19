# Automations

Settings → Automations schedules a prompt to be sent to a model on a repeating basis (once,
daily, weekly, or a cron expression) and records what came back. Pick which tools that run may
use from the same list as chat (memory, documents, tasks, email, notes, web search, and - if you
choose to enable it - shell). There's no separate "run a shell command" task type;
a task is "send a prompt, with these tools available" and reuses the model's
normal tool-calling for that, one mechanism rather than two. The same warning as the chat
toggle applies: only give an automation the shell tool if it isn't also going to encounter
untrusted content - an automation runs unattended, so there is nobody to answer an approval card.
A scheduled task follows its owner's stored approval setting and refuses at once a command that needs approval;
with approval switched off, its commands run straight away, except once the task has read untrusted
content, when it refuses them. The [tool levels](tools.md#tool-levels-for-local-models) apply to automations too.
