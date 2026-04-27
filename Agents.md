# Agents.md

## General Rules
* Make the minimal change possible to accomplish the given tasks
* Follow existing coding standards and naming conventions
* For each task, stage and commit only the relevant files with a relevant message
* Never commit any files that are in the .gitignore file

## Commit Rules
* Treat each requested functional change as its own commit unless the user explicitly asks for a single combined commit
* When a request contains multiple features, bug fixes, hardening tasks, or documentation updates, split the work into focused commits by behavior
* Do not mix unrelated behavior changes in the same commit, even if they were requested in the same prompt
* Keep mechanical follow-up edits with the commit for the behavior they support, such as templates, CSS, migrations, tests, and README notes
* If a later request modifies a previous feature, create a new follow-up commit describing the correction rather than rewriting prior commits unless the user asks for history cleanup
* If there are already staged changes, inspect them before committing and preserve the user's staging intent
* Do not create a commit for instruction-only changes to this file unless the user explicitly asks for it

## Expected Functional Commit Splits
* Collection field type support: commit schema/helpers, form rendering, display formatting, and field type UI together
* Image cropping fixes: commit crop interaction, zoom/pan behavior, autosave, and related image settings UI together
* Collection table layout: commit full-width table, horizontal scrolling, and column sizing together
* User applications: commit application form, applicant storage, admin approval/decline UI, and login links together
* Gallery and item image workflow: commit multi-image selection, per-image crop/order controls, gallery slideshow behavior, and item-level gallery layout together
* Item image deletion: commit image delete routes and image settings delete controls together
* Collection item deletion: commit item delete route, UI, confirmation, and file cleanup together
* Item image reordering: commit drag reorder, order-number insertion behavior, autosave, and related card styling together
* Collection field management: commit field rename/type/position/delete routes and admin UI together
* Item duplication: commit duplicate route and item UI action together
* Advanced filters: commit filter parsing, filtering behavior, and collection filter controls together
* Public user galleries: commit gallery directory route, navigation, template, and public gallery access behavior together
* Reverse proxy and public hardening: commit proxy support, secure cookies, CSRF, rate limiting, safe redirects, public image authorization, secure defaults, and security headers together
* Documentation updates: commit README or deployment docs separately from application behavior unless the docs are necessary to explain new runtime configuration
