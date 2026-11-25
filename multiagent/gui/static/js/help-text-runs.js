// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the runs: list, detail, counters, human actions, external output and the run thread.
// One part of the help catalog: help-text.js joins every part into HELP, so a view's texts live in one file.
// Strict JSON under the rules of help-text.js: { title, hint, more }, keys as the views pass them to <Help topic=...>.

export const HELP = {
  "runs.section": {
    "title": "Runs",
    "hint": "Start framework workflows here and decide what happens at each step.",
    "more": [
      "A run is a workflow with its inputs that advances one step at a time.",
      "Saved runs appear on the left, newest first; on the right, you can see the selected run or the form for a new one.",
      "When a step needs a person, the run waits here for your decision."
    ]
  },
  "runs.new": {
    "title": "New run",
    "hint": "Open the form to start a workflow.",
    "more": [
      "Choose a workflow, fill in its inputs, and select the run mode.",
      "You can preview the prompt before creating anything; the run is created only when you select the Create run button."
    ]
  },
  "runs.list": {
    "title": "Run list",
    "hint": "Saved runs, newest first.",
    "more": [
      "Each row shows the workflow, project, status, and creation time.",
      "If a run came from a bundle, its name appears at the end of the row.",
      "Select a row to open its details."
    ]
  },
  "runs.back": {
    "title": "Back to the list",
    "hint": "Close the details and return to the run list.",
    "more": [
      "On narrow screens, this shows the run list again.",
      "It does not stop or change the open run."
    ]
  },
  "run.open_chat": {
    "title": "Open in chat",
    "hint": "Open this run as a thread in the chat view.",
    "more": [
      "It shows the steps and pending decisions as a conversation.",
      "The current run is kept; no new run is created."
    ]
  },
  "human.bar_focus": {
    "title": "Go to actions",
    "hint": "Move the view to the human actions card.",
    "more": [
      "It takes you to the top of the details, where all available options are shown.",
      "Use it to request changes, reject, or paste a response."
    ]
  },

  "run.refresh": {
    "title": "Refresh",
    "hint": "Request the run's current status from the server again.",
    "more": [
      "This is usually unnecessary: the screen updates automatically when something changes.",
      "Use it if you see “Live updates disconnected” and want to check the status now."
    ]
  },
  "run.live": {
    "title": "Live updates",
    "hint": "Shows whether the screen receives changes as they happen.",
    "more": [
      "“Live” means the server notifies the screen as soon as the run changes.",
      "When live updates are disconnected, the screen refreshes every 10 seconds until the connection returns.",
      "Bundle runs are also checked every 10 seconds."
    ]
  },
  "run.steps": {
    "title": "Steps",
    "hint": "The workflow's steps and its current position.",
    "more": [
      "Each step shows its status: done, in progress, pending, waiting for a person, or skipped.",
      "For completed steps, you can see which model or person produced the result and how many attempts it took.",
      "A skipped step does not run or use model calls."
    ]
  },
  "run.skipped": {
    "title": "Skipped steps",
    "hint": "A step is skipped when its condition is not met.",
    "more": [
      "Some steps apply only when the run requests web search, or only when it does not; the workflow declares this condition.",
      "If the condition is not met, the step does not run and the reason appears next to it.",
      "“Will be skipped” is a notice; “Skipped” means it has already happened."
    ]
  },
  "run.role": {
    "title": "Step role",
    "hint": "The approved team position used to run this step.",
    "more": [
      "The role provides its purpose, rules, and skills to the step, and identifies the team member who runs it.",
      "It comes from the approved team version linked when the run was created, not from later changes."
    ]
  },
  "run.operating_mode": {
    "title": "Operating mode",
    "hint": "The mode set when the run was created and whether it came from the run or the team.",
    "more": [
      "“Supervised” requires human review for handoffs between roles and approval for every write.",
      "“Autonomous” allows ordinary role writes, but destructive and external actions still wait for approval.",
      "The mode is fixed when the run is created and does not change if another team is approved later."
    ]
  },
  "run.counters": {
    "title": "Counters",
    "hint": "How much of each run limit has been used.",
    "more": [
      "The limits prevent loops and unexpected costs.",
      "When model calls run out, you can no longer request changes or regenerate; you can only approve, reject, or cancel.",
      "Counters update as the run progresses."
    ]
  },
  "counter.model_calls": {
    "title": "Model calls",
    "hint": "How many times the framework called a model, compared with the limit.",
    "more": [
      "Only calls made by the framework count; steps completed by a person or external model (guided mode) do not.",
      "Repeating a step because of requested changes or regeneration makes another model call and adds to the count."
    ]
  },
  "counter.human_steps": {
    "title": "Steps completed by a person",
    "hint": "Steps completed by copying and pasting, without calling a model.",
    "more": [
      "These occur when the run is in guided mode or when a step needs external research.",
      "They do not use model calls."
    ]
  },
  "counter.steps": {
    "title": "Steps",
    "hint": "Completed steps out of the total, and how many were skipped.",
    "more": [
      "“Done” counts only steps that finished; skipped steps are shown separately in parentheses.",
      "A step is skipped when its condition is not met, for example, when web search is not requested."
    ]
  },
  "counter.time": {
    "title": "Active time",
    "hint": "How many seconds the server has worked on this run, compared with the limit.",
    "more": [
      "Time spent waiting for a person does not count.",
      "The run stops if it exceeds the limit."
    ]
  },
  "run.history": {
    "title": "Human action history",
    "hint": "What decisions were made and who made them.",
    "more": [
      "Each entry shows when it happened, who acted, and what they did: approve, request changes, reject, regenerate, cancel, or submit a pasted response.",
      "“System” marks automatic actions, “Operator” marks actions taken on this screen, and “Declared” marks information a person provided but that was not verified.",
      "Comments and errors appear beneath their entry."
    ]
  },
  "run.results": {
    "title": "Results",
    "hint": "What each step produced, grouped by step and attempt.",
    "more": [
      "Each step groups its completed attempts, who produced each one, its review, and its output.",
      "When a step has two or more attempts, select two and choose “Compare” to see what changed.",
      "When the run is waiting for your review, the output for the step under review opens here."
    ]
  },
  "run.compare_select": {
    "title": "Attempts to compare",
    "hint": "Choose the two attempts of this step to compare.",
    "more": [
      "The first is the earlier reference; the second is the newer attempt.",
      "The two most recent completed attempts are selected by default."
    ]
  },
  "run.compare": {
    "title": "Compare attempts",
    "hint": "Show which fields changed between the selected attempts.",
    "more": [
      "Compare the outputs field by field, showing additions, removals, and changes.",
      "Long or multiline text is compared line by line."
    ]
  },
  "run.inputs": {
    "title": "Run inputs",
    "hint": "The data used to create this run.",
    "more": [
      "These are the values entered in the form; they are read-only here.",
      "Each name corresponds to a {name} placeholder in the prompts.",
      "“(empty)” means no value was entered."
    ]
  },
  "run.events": {
    "title": "Technical events",
    "hint": "The framework's internal activity log.",
    "more": [
      "Each status change creates a timestamped event; the rest of the view is derived from these events.",
      "This log is for diagnosing problems; the steps and history are usually enough for day-to-day use.",
      "Only the most recent events are shown."
    ]
  },

  "human.section": {
    "title": "Human actions",
    "hint": "What you can do with this run right now.",
    "more": [
      "The available buttons depend on what the run is waiting for: review a result, complete a step outside the framework, or take no action.",
      "The server decides which actions are available; if an action is no longer possible, the view refreshes and explains why.",
      "Every action is recorded in the history."
    ]
  },
  "human.approve": {
    "title": "Approve",
    "hint": "Accept the step's result and continue to the next step.",
    "more": [
      "If this was the last step, the run finishes as complete.",
      "The approved result is marked in “Results”."
    ]
  },
  "human.tool_approve": {
    "title": "Approve this call",
    "hint": "Allow the step to use the tool once with the details shown above.",
    "more": [
      "Approval applies only to this call; another call will need approval again.",
      "If the call details change, the approval no longer applies and approval is requested again."
    ]
  },
  "human.regenerate": {
    "title": "Regenerate without comments",
    "hint": "Run the step again without giving it guidance.",
    "more": [
      "Use this when the result does not satisfy you but you are not sure what to correct.",
      "This calls the model again, counts as another call, and may produce a different result.",
      "This option is unavailable when the budget is exhausted."
    ]
  },
  "human.changes": {
    "title": "Request changes",
    "hint": "Run the step again with your guidance on what to correct.",
    "more": [
      "Write general guidance, add comments on specific parts, or do both.",
      "Use this when the result is on the right track but something specific needs correction.",
      "This counts as another model call and is unavailable when the budget is exhausted."
    ]
  },
  "human.comment_add": { "title": "Add comment", "hint": "Add guidance about one part of the output.", "more": ["You can add up to twenty comments.", "Each comment can refer to a different part."] },
  "human.comment_target": { "title": "Output part", "hint": "Choose General or enter a path such as items[2].title.", "more": ["Visible keys appear in the list.", "You can also enter a more specific path."] },
  "human.comment_kind": { "title": "Change type", "hint": "Say whether to fix, add, remove, or rewrite something.", "more": ["Choose the type that best describes your request.", "Choose Other for a different kind of change."] },
  "human.comment_text": { "title": "Comment text", "hint": "Explain the change you want.", "more": ["Every comment must include text.", "It will be sent with the next attempt."] },
  "human.comment_quote": { "title": "Quote from the output", "hint": "Optional: copy a fragment of the result you are referring to.", "more": ["A quote helps identify the fragment.", "It is provided as untrusted data."] },
  "human.comment_remove": { "title": "Remove comment", "hint": "Remove this row before submitting the request.", "more": ["This removes it only from the current form.", "Other comments remain."] },
  "human.reject": {
    "title": "Reject run",
    "hint": "End the run as rejected; this cannot be undone.",
    "more": [
      "Use this when the result is not useful and you do not want to continue trying.",
      "You can add an optional reason, which is recorded in the history.",
      "You will be asked to confirm before rejecting."
    ]
  },
  "human.reject_reason": {
    "title": "Rejection reason",
    "hint": "Optional: explain why you are rejecting the run.",
    "more": [
      "The reason is recorded in the history with your rejection.",
      "You can leave it blank."
    ]
  },
  "human.cancel": {
    "title": "Cancel run",
    "hint": "Stop the run before it finishes.",
    "more": [
      "If it is running, cancellation is requested and takes effect when the current step finishes.",
      "If it is waiting for a person, it is cancelled immediately.",
      "This cannot be undone, so you will be asked to confirm."
    ]
  },
  "human.prompt": {
    "title": "Prompt to copy",
    "hint": "The exact text to paste into another tool.",
    "more": [
      "Copy it as-is into the model or tool you prefer.",
      "It already includes your inputs and, at the end, instructions for the response format.",
      "It is read-only so you do not change it by accident."
    ]
  },
  "human.copy_prompt": {
    "title": "Copy prompt",
    "hint": "Copy the prompt to the clipboard.",
    "more": [
      "This saves you from selecting the text by hand.",
      "If the browser blocks clipboard access, the prompt is selected; press Ctrl+C to copy it."
    ]
  },
  "human.response": {
    "title": "Pasted response",
    "hint": "Paste the complete response from the other tool here.",
    "more": [
      "The response must match the format shown above.",
      "The server validates it when submitted; if it does not match, it is rejected and the text stays here for you to correct.",
      "Your draft remains if you switch screens, unless you reload the page."
    ]
  },
  "human.provider": {
    "title": "Provider",
    "hint": "Who produced the response, such as the tool you used.",
    "more": [
      "You provide this value and it is not verified; the history marks it as “Declared”.",
      "If you leave it blank, “human” is recorded.",
      "This helps identify where each result came from."
    ]
  },
  "human.model": {
    "title": "Model",
    "hint": "Optional: the name of the model that responded.",
    "more": [
      "You provide this value too, and it is not verified.",
      "If you do not know it, leave it blank; the history will say “model not specified”."
    ]
  },
  "human.prompt_used": {
    "title": "Prompt used",
    "hint": "Optional: record the exact prompt if you changed it before using it elsewhere.",
    "more": ["If you leave it blank, the prompt shown for this step is saved.", "It is associated with the submitted response."]
  },
  "human.notes": {
    "title": "Notes",
    "hint": "Optional: add context about how you got the response.",
    "more": ["Notes are saved with the submission.", "They do not affect contract validation."]
  },
  "external.section": {
    "title": "External output",
    "hint": "Submit output produced outside the framework, along with its evidence.",
    "more": ["First, review a preview that makes no changes; then confirm it.", "Confirmation saves a receipt with hashes for everything submitted."]
  },
  "external.output": {
    "title": "JSON output",
    "hint": "The JSON object that satisfies the waiting step's contract.",
    "more": ["Paste the complete object; the preview validates it against the contract.", "If there is an error, your text remains on screen so you can correct it."]
  },
  "external.evidence": {
    "title": "Evidence",
    "hint": "Optional files that support the output.",
    "more": ["Images, short videos, text, and JSON are accepted within the size limits.", "The page shows only each file's name, type, size, and hash, never its contents."]
  },
  "external.preview": {
    "title": "Preview",
    "hint": "Validate the output and evidence without changing the run.",
    "more": ["The preview shows normalized output, each file with its hash, and when the preview expires.", "Prepare another preview if it expires."]
  },
  "external.confirm": {
    "title": "Confirm external output",
    "hint": "Submit the output shown in the preview.",
    "more": ["You must confirm before anything is saved.", "The receipt then shows who submitted it, through which channel, and when."]
  },
  "human.check": {
    "title": "Validate without submitting",
    "hint": "Check the JSON, contract, and fields before submitting.",
    "more": ["Problems are shown by field without saving the response or advancing the run.", "You can correct the text and validate it again."]
  },
  "human.submit": {
    "title": "Submit response",
    "hint": "Submit the pasted response for the framework to validate.",
    "more": [
      "If it matches the format, the run continues; depending on the workflow, it may first wait for your review.",
      "If it does not match, it is rejected with a reason and you can correct it.",
      "The submission is recorded in the history with the provider and model you declared."
    ]
  },

  "thread.section": {
    "title": "Run thread",
    "hint": "Shows the steps and decisions for this run.",
    "more": ["The thread refreshes every few seconds while this view is open.", "Decision controls appear at the current pause."]
  },
  "thread.refresh": {
    "title": "Refresh thread",
    "hint": "Read all messages for this run again.",
    "more": ["Use this to check the status now.", "It does not change the run."]
  },
  "thread.detail": {
    "title": "View run details",
    "hint": "Open the full details in Runs.",
    "more": ["There you can review results and actions that are not shown in this thread.", "The link keeps the selected run."]
  },
  "thread.related": {
    "title": "Open related item",
    "hint": "Open the plan or team linked to this run.",
    "more": ["The link appears when the run belongs to a task or uses a team.", "Opening it does not change the run."]
  },
  "thread.approve": {
    "title": "Approve the pause",
    "hint": "Approve the result or pending call.",
    "more": ["Read the details before deciding.", "Approval is submitted only after you confirm."]
  },
  "thread.changes": {
    "title": "Request changes",
    "hint": "Send the step back with guidance or comments.",
    "more": ["Write guidance or add a comment.", "Confirm before submitting them."]
  },
  "thread.reject": {
    "title": "Reject the run",
    "hint": "End this run after confirming.",
    "more": ["You can provide a reason.", "A rejection cannot be undone."]
  },
  "thread.feedback": {
    "title": "Decision guidance",
    "hint": "Explain what should change or why you are rejecting it.",
    "more": ["Requesting changes requires guidance or a comment.", "A rejection reason is optional."]
  },
  "thread.quote": {
    "title": "Quote",
    "hint": "Copy the text you selected into your message, as a quote.",
    "more": ["Select text in the output first.", "It is added as plain text before what you wrote."]
  },
  "thread.comment": {
    "title": "Review comment",
    "hint": "Identify a part, the type of change, and the details.",
    "more": ["You can add multiple comments.", "Quoting the output is optional."]
  },
  "thread.confirm": {
    "title": "Confirm decision",
    "hint": "Submit the decision or go back without changing the run.",
    "more": ["Check the result and guidance before submitting.", "The thread refreshes after the action."]
  },
  "thread.output": {
    "title": "Output to review",
    "hint": "The saved output from the attempt waiting for your decision.",
    "more": ["Fields appear as a list and lists of objects appear as tables.", "Text and full JSON are displayed as plain text."]
  },
  "thread.json": {
    "title": "Full JSON",
    "hint": "Open the original output with all its fields.",
    "more": ["It is collapsed so you can read the summary first.", "Markdown and HTML are not rendered."]
  },
  "thread.document": {
    "title": "Document",
    "hint": "The run's result, written as a Markdown document.",
    "more": ["It is shown as plain text, without rendering the formatting.", "It appears when the run is complete and has a result."]
  },
  "thread.document_save": {
    "title": "Save as Markdown",
    "hint": "Download the document as a .md file in your browser.",
    "more": ["Your browser creates the file; the server does not write to your computer.", "The filename includes the title and the beginning of the run ID."]
  },
  "thread.previous": {
    "title": "View earlier steps",
    "hint": "Load saved outputs from earlier steps in this run.",
    "more": ["They are loaded only when you select the button.", "Each attempt can be opened separately."]
  },

  "inbox.section": {
    "title": "Message the run", "hint": "Write an instruction for the next work step.",
    "more": ["The inbox works only when the availability policy is enabled.", "The message is read at a work boundary and never approves a decision."]
  },
  "inbox.kind": {
    "title": "Message action", "hint": "Choose whether to cancel, interrupt, adjust, or leave a note.",
    "more": ["Cancel stops the run when the worker reads the message.", "Interrupt and adjust wait for your decision; a note records information and lets work continue."]
  },
  "inbox.text": {
    "title": "Message text", "hint": "Explain your adjustment or leave a note.",
    "more": ["Text is required for an adjustment or a note.", "The inbox accepts up to 1,200 bytes of text."]
  },
  "inbox.send": {
    "title": "Send message", "hint": "Save the message for the worker to read.",
    "more": ["Cancel, interrupt, and adjust require extra confirmation.", "The button is disabled if the policy is off or the run has ended."]
  },
  "inbox.confirm": {
    "title": "Confirm message", "hint": "Confirm the instruction you are about to save.",
    "more": ["The message is saved with its own ID to prevent duplicates.", "It takes effect when the worker reaches the next work boundary."]
  },
  "inbox.back": {
    "title": "Back to message", "hint": "Close the confirmation and keep the text.",
    "more": ["You can change the action or text before sending.", "The run stays in its current state."]
  },

  "human.tool_preview": {
    "title": "file.write preview",
    "hint": "Show the proposed file contents before approving the call.",
    "more": [
      "If the file exists, the preview compares its current contents with the proposed contents and warns if lines will be removed.",
      "The preview uses arguments reconstructed from the workflow and shows them only if they match the pending approval fingerprint.",
      "If they do not match or the path is outside the allowed limits, it explains why the contents cannot be shown."
    ]
  },

  "run.limit_retry": {
    "title": "Retry after a limit",
    "hint": "Open “New session” to launch again when the quota resets.",
    "more": [
      "When a run fails because of a quota, it shows “Limit reached” with the local time or “time unknown”.",
      "Selecting “Retry” opens a new session; nothing is relaunched automatically."
    ]
  },
  "run.tabs": {
    "title": "Run sections",
    "hint": "Switch between this run's steps, results, and details.",
    "more": [
      "“Steps” shows progress and counters; “Results” groups outputs by step and attempt.",
      "“Details” contains the human action history, inputs, and technical events."
    ]
  }
};
