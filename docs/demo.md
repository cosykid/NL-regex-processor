# Demo

> Record a short screen capture of an asynchronous job running to completion and
> embed or link it here before submitting.

Suggested flow to capture:

1. Upload a CSV (e.g. the sample, or a generated large file from
   `scripts/generate_dataset.py`).
2. Pick the target column(s), type a natural-language pattern
   (e.g. *"Find email addresses"*) and a replacement value. For multi-column
   matching, select several columns and describe a cross-column condition
   (e.g. *"name starts with A and phone starts with 0"*).
3. Show the job moving through **QUEUED → RUNNING** with the live progress bar
   and the resolved conditions appearing (per-column predicates + AND/OR).
4. Show the paginated processed results once the job reaches **SUCCESS**.
5. (Optional) Start a second job and **cancel** it mid-run to show cancellation.

Embed example (GitHub renders MP4 links as players when uploaded to a release or
issue):

```md
https://user-images.githubusercontent.com/.../demo.mp4
```
