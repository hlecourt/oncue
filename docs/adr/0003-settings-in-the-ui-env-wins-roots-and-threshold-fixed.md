# Settings are editable in the UI, environment variables win, allowed folders and the confidence threshold are not editable

Settings edited in the UI are stored in the state folder, but an environment variable always wins and its field shows as locked, so a compose file stays the source of truth for those who use one. Two things are deliberately not editable in the UI: the allowed folders, because they are the boundary of where the guard may write and a stolen session must not be able to widen it, and the high-confidence threshold, because it is the product's promise and a setting would invite lowering it.

## Considered Options

- Environment only with a read-only System page: simpler, but the owner wants a real settings page in v1.
- Everything editable including allowed folders and the threshold: rejected for the two reasons above.
