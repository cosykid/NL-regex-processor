# Rhombus AI
## Technical Assessment - Distributed NL-to-Regex Data Processing Platform

### Objective
You are tasked with building a web application using Django and React that lets users upload CSV or Excel files, identify patterns in text columns using natural-language input, and replace the matched patterns at scale. 

Your system must process files asynchronously and be capable of handling large datasets (millions of rows) without blocking the request/response cycle. To do this you will use Celery for distributed task execution, Redis as the message broker, result backend and cache, and PySpark for the distributed data-transformation engine.

---

### Requirements

#### 1. Backend Development with Django
* Set up a Django project structured for data processing with clear separation between API, task, and data layers.
* Implement models, views and URLs to handle uploads, job tracking, and result retrieval. 
* Jobs must be persisted with status (`QUEUED`, `RUNNING`, `SUCCESS`, `FAILED`) and progress.
* Expose a REST API that accepts natural-language input, converts it to a regex pattern using an LLM, and dispatches the replacement work to a background worker. The endpoint must return immediately with a job ID—it must not block on processing.
* Provide endpoints to poll job status / progress and to fetch the processed result once complete.

#### 2. Asynchronous Processing with Celery & Redis
* Use Celery to run all file parsing, regex generation and replacement work as background tasks. The web process must never perform heavy processing inline.
* Use Redis as the Celery message broker and result backend. Document your broker/backend configuration.
* Use Redis as a cache layer at minimum; cache LLM-generated regex patterns keyed by the natural-language prompt so identical requests are not re-sent to the LLM.
* Report task progress back to the user (e.g., percentage of rows processed) via Celery state updates surfaced through the polling API.
* Handle task failure, retries with backoff, and cancellation of a running job gracefully.

#### 3. Distributed Data Processing with PySpark
* Implement the core pattern-matching and replacement engine using PySpark so it scales horizontally across partitions rather than iterating row-by-row in pandas.
* Apply the LLM-generated regex as a Spark transformation over the target column(s). Your solution should remain correct and performant as row count grows into the millions.
* Read the uploaded file into Spark, perform the replacement, and write the result back in a format the frontend can render (paged/streamed—do not attempt to return millions of rows to the browser at once).
* Briefly justify your partitioning/parallelism choices in the README.

#### 4. Frontend Development with React
* Develop a clean interface allowing users to upload CSV/Excel files.
* Provide input fields for users to describe the pattern in natural language, specify the replacement value, and choose the target column(s).
* Because processing is asynchronous, the UI must show live job status and progress, then display the processed data (with pagination) once the job completes.
* Handle long-running jobs, errors, and empty/edge-case results without freezing the UI.

#### 5. LLM Integration
* Use a Large Language Model to convert natural-language input into a regex pattern, and ensure it handles varied descriptions accurately.
* Validate / sanitize the generated regex before applying it (guard against catastrophic backtracking and invalid patterns).
* Cache results in Redis as noted above.
* **[OPTIONAL]** Showcase creativity by using the LLM for two additional data transformations of your choice, executed through the same async/Spark pipeline.

---

### Task Description

#### 1. File Upload
* Users upload CSV or Excel files. The file is ingested asynchronously; large files must not be loaded entirely into the web process.
* Once processed, data is displayed in a paginated tabular format.

#### 2. Pattern Matching and Replacement
* Users describe the pattern in natural language (e.g., *"find email addresses"*).
* The application uses an LLM to convert this into a regex pattern (cached in Redis), dispatches a Celery task that applies the pattern via a PySpark transformation across the target column(s), replaces matches with the user-specified value, and surfaces progress until the updated data is ready to view.

---

### Example Scenario

#### Sample Input Data

| Name | ID | Email |
| :--- | :--- | :--- |
| John Doe | 1 | john.doe@example.com |
| Jane Smith | 2 | jane_smith@domain.com |
| Alice Brown | 3 | alice.brown@website.org |

* **User Input - Natural Language:** "Find email addresses in the Email column and replace them with 'REDACTED'."
* **LLM Output - Regex Pattern:** `\b[A-Za-z0-9.%+]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,7}\b` *(Note: Cleaned up spacing/brace OCR artifact)*
* **Replacement Value:** `'REDACTED'`

#### Processed Output Data

| Name | ID | Email |
| :--- | :--- | :--- |
| John Doe | 1 | REDACTED |
| Jane Smith | 2 | REDACTED |
| Alice Brown | 3 | REDACTED |

---

### Development Guidelines
* Write clean, maintainable, and well-documented code with a clear, deliberate architecture—software design is critical and weighted heavily.
* Include reasonable error handling and validation across the frontend, API, Celery tasks, and Spark jobs.
* Provide a `docker-compose` setup that brings up the web app, Celery worker(s), Redis, and the Spark runtime so the system can be run end-to-end with a single command.
* Demonstrate that your pipeline handles large files; include or describe a test with a sizeable dataset.
* **[OPTIONAL]** Add basic observability (task metrics, worker monitoring e.g., Flower) and tests for the task/Spark layer.

### Deliverables
* **Source Code:** Submit the complete source code via a GitHub repository.
* **README File:** A detailed `README.md` including setup/run instructions (including the async/Spark stack), an overview of your architecture and the reasoning behind it, and any notes or trade-offs.
* **Deployment:** Deploy the full application to a publicly accessible environment and provide a working URL where it can be tested end-to-end.
* **Demo Video:** A short video demonstrating the app in action including an asynchronous job running to completion embedded in the `README.md`.
