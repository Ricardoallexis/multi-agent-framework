// Copyright 2026 Ricardoallexis and contributors
// SPDX-License-Identifier: Apache-2.0
// Help texts of the project view (PRY-T03): strict JSON, every key under "project." (see js/help-text.js for the rules).

export const HELP = {
  "project.view": {
    "title": "Projects",
    "hint": "Track a project's progress by stages, phases, and tasks.",
    "more": [
      "Everything shown here comes from the framework; no data is made up.",
      "You can approve a submitted task or request changes here. Everything else is read-only."
    ]
  },
  "project.select": {
    "title": "Project",
    "hint": "Choose an active project to view.",
    "more": [
      "The list shows projects that are not archived.",
      "Choosing another project closes the task details you had open."
    ]
  },
  "project.refresh": {
    "title": "Refresh",
    "hint": "Load the project's data from the server again.",
    "more": [
      "The screen updates by itself while other people or agents work; Refresh asks for it now.",
      "Use this button to see the latest changes."
    ]
  },
  "project.tabs": {
    "title": "Sections",
    "hint": "Switch between the overview, stages and tasks, and the flat list.",
    "more": [
      "Overview shows progress and what needs your attention.",
      "Stages and tasks shows each stage, its phases, filters, and task details.",
      "The flat list shows all tasks with filters for owner, state, and due date."
    ]
  },
  "project.progress": {
    "title": "Progress",
    "hint": "Completed tasks out of those that count, and the number in each state.",
    "more": [
      "Cancelled tasks do not count toward progress.",
      "A task is complete when it is accepted, not when it is submitted."
    ]
  },
  "project.attention": {
    "title": "What needs your attention",
    "hint": "Items waiting for a decision or action from you.",
    "more": [
      "This section includes submitted tasks to review, stages to verify, and your own tasks.",
      "Each blocker shows the task it depends on and its state, so you know what to move forward."
    ]
  },
  "project.working_now": {
    "title": "Who is working now",
    "hint": "Tasks and activities in progress, with their owner.",
    "more": [
      "This includes work that someone picked up, is doing, paused, or that is being checked automatically.",
      "If there is no owner, no one has picked up the task yet.",
      "Activities appear here but do not count toward progress or state totals."
    ]
  },
  "project.milestones": {
    "title": "Stages and milestones",
    "hint": "The planned date and actual completion date for each stage.",
    "more": [
      "The planned date comes from the plan and may be missing.",
      "The actual completion date is recorded when the stage is verified and closed."
    ]
  },
  "project.stages_view": {
    "title": "Stages and tasks",
    "hint": "Each stage with collapsible phases and their tasks.",
    "more": [
      "Open a task to see its dependencies and submitted work.",
      "Tasks without a phase appear separately under No phase."
    ]
  },
  "project.filter_text": {
    "title": "Search",
    "hint": "Filter by text in the title, ID, or phase.",
    "more": [
      "Search is not case-sensitive.",
      "This only changes what you see; it does not change any tasks."
    ]
  },
  "project.filter_state": {
    "title": "Filter by state",
    "hint": "Show only tasks with the selected state.",
    "more": [
      "States come from the framework, such as Waiting for your review or Blocked.",
      "Choose All to clear the filter."
    ]
  },
  "project.list_view": {
    "title": "Task list",
    "hint": "All project tasks in one flat list.",
    "more": [
      "Each row shows its stage, owner, due date, and the stage's planned dates.",
      "Tasks without a date say No date; dates are never made up."
    ]
  },
  "project.list_owner": {
    "title": "Filter by owner",
    "hint": "Show only tasks owned by the selected person or agent.",
    "more": [
      "The owner is the person who picked up the task, or the suggested owner if no one has.",
      "Choose All to clear the filter."
    ]
  },
  "project.list_state": {
    "title": "Filter by state",
    "hint": "Show only tasks with the selected state.",
    "more": [
      "States come from the framework and are also shown in Activity.",
      "Choose All to clear the filter."
    ]
  },
  "project.list_dates": {
    "title": "Filter by due date",
    "hint": "Show only tasks due between the two dates.",
    "more": [
      "Both dates are optional, and the selected days are included.",
      "When either date is set, tasks without a due date are hidden."
    ]
  },
  "project.phase_toggle": {
    "title": "Collapse phase",
    "hint": "Show or hide this phase's tasks.",
    "more": [
      "Collapsing a phase does not change the project.",
      "The number in parentheses shows tasks matching the filter and the total."
    ]
  },
  "project.task_detail": {
    "title": "Task details",
    "hint": "Owner, due date, dependencies, and submitted work.",
    "more": [
      "Dependencies show what must happen first and whether it has happened.",
      "Deliverables come from the latest result submitted by the task's owner."
    ]
  },
  "project.deliverables": {
    "title": "Deliverables",
    "hint": "The latest result submitted by the person who did the task.",
    "more": [
      "This includes the summary, files, decisions, and anything not tested.",
      "Read it before approving or requesting changes."
    ]
  },
  "project.approve": {
    "title": "Approve",
    "hint": "Accept the result and mark the task as complete.",
    "more": [
      "The task is accepted, and tasks that depend on it can move forward.",
      "If someone changed the task while you were viewing it, the server rejects the decision and you must review it again."
    ]
  },
  "project.request_changes": {
    "title": "Request changes",
    "hint": "Send the task back to its owner with your feedback.",
    "more": [
      "The task moves to Changes requested, and its owner must work on it again.",
      "You must explain what needs to change."
    ]
  },
  "project.feedback_input": {
    "title": "Feedback",
    "hint": "Write what needs to be fixed or completed.",
    "more": [
      "This text is saved as the reason for the state change.",
      "You can enter up to 500 characters."
    ]
  },
  "project.send_feedback": {
    "title": "Send feedback",
    "hint": "Request changes using the text you wrote.",
    "more": [
      "Your decision is recorded, and the task goes back to its owner.",
      "Nothing is sent without text."
    ]
  },
  "project.cancel_feedback": {
    "title": "Cancel",
    "hint": "Close the form without requesting changes.",
    "more": [
      "The task will still be waiting for your review.",
      "Your text is discarded."
    ]
  },
  "project.slot": {
    "title": "Project",
    "hint": "Project progress and what needs your attention.",
    "more": [
      "Shows the project open on the Projects screen, or the first project that needs your attention.",
      "Data loads when the panel opens and updates when you make a decision on the Projects screen."
    ]
  },
  "project.open": {
    "title": "Open project",
    "hint": "Go to the Projects screen for this project.",
    "more": [
      "There you can see stages, tasks, and their deliverables.",
      "This link does not change the project."
    ]
  }
};
