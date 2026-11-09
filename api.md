# Expense Policy Validator API

API reference • Version 0.1.0 • Generated November 9, 2026

Generated from the application’s OpenAPI schema. Includes every operation, parameter, request body, response and component schema. Deployment settings may restrict runtime availability.

65 operations • 130 schemas

## Contents

record management: 10 operations

documents: 22 operations

evaluations: 13 operations

rulesets: 12 operations

health: 1 operations

validation: 2 operations

runs: 3 operations

discovery: 2 operations

Component schemas follow the endpoint reference. Schema names in type columns refer to that section.

# record management

## DELETE /v1/rulesets/{ruleset_id}/rules/{rule_id}

Remove Rule

Create a new draft with the rule inactive; published artifacts remain immutable.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |
| rule_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | RuleRemoval<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## PATCH /v1/documents/extractions/{extraction_id}/reviews/{review_id}

Revise Review

Record revised decisions under a new review identity, retaining the original signed review.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |
| review_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ReviewCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## DELETE /v1/documents/extractions/{extraction_id}/reviews/{review_id}

Delete Review

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |
| review_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## PATCH /v1/evaluations/datasets/{dataset_id}

Edit Dataset

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | DatasetEdit<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## DELETE /v1/evaluations/datasets/{dataset_id}

Delete Dataset

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## PATCH /v1/evaluations/{evaluation_id}

Edit Evaluation

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | EvaluationEdit<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## DELETE /v1/evaluations/{evaluation_id}

Delete Evaluation

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## DELETE /v1/rulesets/{ruleset_id}

Delete Draft

Hide a draft or in-review version, retaining its immutable artifact and reserving its number.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## GET /v1/rulesets/{ruleset_id}/duplicate-publication

Duplicate Publication

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## POST /v1/rulesets/{ruleset_id}/duplicate-publication

Publish Duplicate

Publish an identical successor with verified source provenance and a fresh approval; retain the original.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | DuplicatePublication<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# documents

## GET /v1/documents/extractions/{extraction_id}/reviews/{review_id}

Get Review

One stored review with its decisions, as sent: to continue a review where it was left.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |
| review_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ReviewDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## POST /v1/documents/extraction-plan

Plan Extraction

Resolve IDs, revisions, baseline and rule scope without extraction, provider calls or database writes.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ExtractionCreate<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ExtractionPlanSummary<br> |
| 403 | Forbidden<br>application/json: ErrorResponse<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents/extractions/{extraction_id}/ruleset

Extraction Version

Create a new version from any extraction mode; optionally accept, approve and publish when review is ready.

Name defaults to the resolved baseline's name (or policies). Review defaults to the latest saved review.
Compiler validation and publication-mode safeguards apply; unresolved decisions are never guessed.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ExtractionVersionCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents/extractions

Create Extraction

Extract interpretations of exact document revisions with the core's PolicyCompiler, and store the snapshot.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ExtractionCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: ExtractionSummary<br> |
| 403 | Forbidden<br>application/json: ErrorResponse<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents/extractions

List Extractions

Extractions, newest first, with their reviews. The core's stored results are not read: GET one for its review_required fields.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string \| null | No | query<br>Only extractions that read a revision of this document<br>Only extractions that read a revision of this document |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ExtractionPage<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents/extraction-jobs

Start Extraction Job

Start an extraction in the background, after checking it can run; follow it with GET /extraction-jobs/{job_id}.

A live extraction reads the documents one at a time, so its progress names each document. Use this rather than
POST /extractions for a live extraction: it is never cut off by a proxy's request timeout.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ExtractionCreate<br> |

| Response | Description / schema |
| --- | --- |
| 202 | Successful Response<br>application/json: ExtractionJob<br> |
| 403 | Forbidden<br>application/json: ErrorResponse<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents/extraction-jobs/{job_id}

Get Extraction Job

Where a background extraction is: its progress, then its extraction_id, or the error that stopped it.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| job_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ExtractionJob<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## GET /v1/documents/extractions/{extraction_id}

Get Extraction

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ExtractionSummary<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents/extractions/{extraction_id}/comparisons

Get Comparisons

Every field comparison, as the core gave it, a page at a time. Nothing is truncated in storage.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |
| review_required | boolean | No | query<br>Only the fields an exceptions_only review must decide<br>Only the fields an exceptions_only review must decide<br>default: false |
| limit | integer | No | query<br><br>default: 100<br>minimum: 1<br>maximum: 500 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: expense_api__schemas__documents__ComparisonPage<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents/extractions/{extraction_id}/review

Review Extraction

Review the snapshot with the core's review contract. Stored whether ready or not; only a ready one can become a candidate.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ReviewCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: ReviewDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents/extractions/{extraction_id}/baseline

Extraction Baseline

This compilation beside the latest published version: what its rules change, which clauses changed, and which
earlier decisions answer the same question (unchanged) or must be made again (changed).

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: Baseline<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## GET /v1/documents/extractions/{extraction_id}/comparisons/{comparison_id}/source

Get Source

The compared field's clause in the exact revision read, with its lines and disputed values when verified.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |
| comparison_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: SourceLocation<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## GET /v1/documents/extractions/{extraction_id}/change-requests

List Change Requests

Every change request raised on this extraction's fields, oldest first, open or resolved.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: array<ChangeRequest><br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## POST /v1/documents/extractions/{extraction_id}/change-requests

Create Change Request

Ask for one compared field to be corrected. Nothing is rewritten: while it is open, no version is made from the
extraction and no version made from it is approved.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ChangeRequestCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: ChangeRequest<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents/extractions/{extraction_id}/change-requests/{change_request_id}/resolve

Resolve Change Request

Close a change request, with a note saying how it was addressed, such as a new revision and extraction.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | path<br><br> |
| change_request_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ChangeRequestResolve<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ChangeRequest<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents

Create Document

Store a Markdown policy document as revision 1, exactly as sent.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | DocumentCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: DocumentDetail<br> |
| 413 | Request Entity Too Large<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents

List Documents

Each document's latest revision, newest document first, without content.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| filename | string \| null | No | query<br><br> |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: DocumentPage<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents/{document_id}

Get Document

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | path<br><br> |
| revision | integer \| null | No | query<br>The latest when left out<br>The latest when left out |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: DocumentDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## PATCH /v1/documents/{document_id}/profile

Update Profile

Change a document's category, description or scope. Its revisions and their text never change.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ProfileUpdate<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: DocumentSummary<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/documents/{document_id}/revisions/{revision}/approval

Get Approval

One exact revision's approval and its whole history. A revision never acted on is a draft.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | path<br><br> |
| revision | integer | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RevisionApproval<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## POST /v1/documents/{document_id}/revisions/{revision}/approval

Act On Approval

Submit, approve, or return to draft one exact revision, named by its content hash. Recorded with who and when.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | path<br><br> |
| revision | integer | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ApprovalAction<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RevisionApproval<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/documents/{document_id}/revisions

Create Revision

Store changed content as the next revision. Earlier revisions never change.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | RevisionCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: DocumentDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 413 | Request Entity Too Large<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# evaluations

## GET /v1/evaluations/datasets/{dataset_id}

Get Dataset

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string | Yes | path<br><br> |
| include_cases | boolean | No | query<br>Include the canonical cases<br>Include the canonical cases<br>default: false |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: DatasetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations/{evaluation_id}

Get Evaluation

Status, progress, configuration and spending by model, backend and call purpose.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: EvaluationDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/evaluations/datasets

Upload Dataset

Store a dataset as a new immutable version of its name. The same bytes again return the stored version.

Request body (required)

| Content type | Schema |
| --- | --- |
| multipart/form-data | Body_upload_dataset_v1_evaluations_datasets_post<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: DatasetSummary<br> |
| 200 | These exact bytes were uploaded before: that version<br>application/json: DatasetSummary<br> |
| 413 | Request Entity Too Large<br>application/json: ErrorResponse<br> |
| 415 | Unsupported Media Type<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations/datasets

List Datasets

Dataset versions, newest first, without their content.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| name | string \| null | No | query<br><br> |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: DatasetPage<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/evaluations

Create Evaluation

Queue an evaluation of one dataset version against one ruleset version. The worker runs it.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | EvaluationCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: EvaluationDetail<br> |
| 200 | The evaluation this idempotency_key already made<br>application/json: EvaluationDetail<br> |
| 403 | Forbidden<br>application/json: ErrorResponse<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations

List Evaluations

Evaluations, newest first, with key metrics and spending but no results.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string \| null | No | query<br><br> |
| ruleset_id | string \| null | No | query<br><br> |
| backend | string \| null | No | query<br><br> |
| model | string \| null | No | query<br>Any model the run may call: a backend's, the reviewer's or the translator's<br>Any model the run may call: a backend's, the reviewer's or the translator's |
| status | string \| null | No | query<br><br> |
| execution_mode | string \| null | No | query<br><br> |
| created_from | string (date-time) \| null | No | query<br>Inclusive, with a time zone<br>Inclusive, with a time zone |
| created_to | string (date-time) \| null | No | query<br>Exclusive, with a time zone<br>Exclusive, with a time zone |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: EvaluationPage<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations/compare/export

Export Comparison

PDF of 2–10 completed evaluations, in requested baseline order, from the same saved comparison.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_ids | array<string> | Yes | query<br><br>minItems: 2<br>maxItems: 10 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br><br>application/pdf: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/evaluations/compare

Compare

Completed evaluations side by side, saying whether they can be compared directly.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | CompareRequest<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations/{evaluation_id}/metrics

Get Metrics

The stored metrics of a finished run, with which scoring version made them; provisional ones, never stored, while it runs.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |
| scoring | string | No | query<br>latest: the latest rescoring, if any; original: as scored when it finished<br>latest: the latest rescoring, if any; original: as scored when it finished<br>enum: ["latest", "original"]<br>default: "latest" |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/evaluations/{evaluation_id}/rescore

Rescore

Score a finished run again with the current scoring version, from its stored results. Its original metrics stay.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations/{evaluation_id}/results

Get Results

Each receipt's core result, whole, with its answers, how it scored and what it cost, in dataset order.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |
| receipt_id | array<string> \| null | No | query<br><br> |
| classification_correct | boolean \| null | No | query<br>true: every labelled line correct; false: any not. Receipts without labelled lines match neither<br>true: every labelled line correct; false: any not. Receipts without labelled lines match neither |
| category | array<string> \| null | No | query<br>A category predicted on any line<br>A category predicted on any line |
| disposition | array<string> \| null | No | query<br>flag, review or pass, as predicted<br>flag, review or pass, as predicted |
| error | boolean \| null | No | query<br>true: the receipt or one of its lines failed<br>true: the receipt or one of its lines failed |
| uncertain | boolean \| null | No | query<br>true: the receipt has an EXC-03 uncertainty signal<br>true: the receipt has an EXC-03 uncertainty signal |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: ResultPage<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/evaluations/{evaluation_id}/export

Export

A finished evaluation as a file: json holds the run, metrics, results and every call; csv one row per line; pdf includes the selected scoring view and saved receipt results.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |
| format | string | No | query<br>json, csv or pdf<br>json, csv or pdf<br>default: "json" |
| scoring | string | No | query<br><br>enum: ["latest", "original"]<br>default: "latest" |

| Response | Description / schema |
| --- | --- |
| 200 | The evaluation as a file<br>application/json: any<br><br>text/csv: any<br><br>application/pdf: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/evaluations/{evaluation_id}/cancel

Cancel

Cancel a queued evaluation, or ask a running one to stop before its next receipt or provider call.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: EvaluationDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# rulesets

## GET /v1/rulesets/{ruleset_id}

Get Ruleset

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/rulesets

Create Ruleset

Store the next draft version of a name: compiled from policy documents, or an uploaded compiled artifact.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | RulesetCreate<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: RulesetDetail<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/rulesets

List Rulesets

List ruleset versions, newest first, with their lifecycle state and without their artifacts.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| name | string \| null | No | query<br>One name's versions<br>One name's versions |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |
| include_superseded | boolean | No | query<br><br>default: false |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RulesetPage<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/rulesets/{ruleset_id}/changes

Get Changes

What this version does differently from the version published before it: each rule field, before and after.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: VersionChanges<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## GET /v1/rulesets/{ruleset_id}/comparisons

Get Comparisons

The compiler's field comparisons for a compiled version: what review decisions name, by comparison_id.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |
| review_required | boolean | No | query<br>Only the fields an exceptions_only review must decide<br>Only the fields an exceptions_only review must decide<br>default: false |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: expense_api__schemas__rulesets__ComparisonPage<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/rulesets/{ruleset_id}/comparisons/{comparison_id}/source

Get Source

A compared field's clause in the exact text this version was compiled from, verified, never approximate.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |
| comparison_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: SourceLocation<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## POST /v1/rulesets/{ruleset_id}/edits

Edit Rules

A new draft version with reviewed changes to some rules. This version, published or not, never changes:
the compiler builds the draft again from the same documents with the changes applied, and validates it.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | RuleEditRequest<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/rulesets/{ruleset_id}/readiness

Get Readiness

What stands between this version and publication now, each blocker linked to its rule or field.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: Readiness<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |
| 422 | Validation Error<br>application/json: HTTPValidationError<br> |

## POST /v1/rulesets/{ruleset_id}/submit

Submit

draft → in_review.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | TransitionRequest<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/rulesets/{ruleset_id}/review

Review

Record a review decision: accepted keeps the version in_review for approval, rejected returns it to draft.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ReviewRequest<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/rulesets/{ruleset_id}/approve

Approve

in_review → approved, once the core's approval of this exact version passes its publication safeguards.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ApproveRequest<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## POST /v1/rulesets/{ruleset_id}/publish

Publish

approved → published, after the approval is checked again. Publishing a published version again is a no-op.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | path<br><br> |

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | PublishRequest<br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RulesetDetail<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# health

## GET /health

Health

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: Health<br> |
| 503 | Running, but MongoDB is unavailable<br>application/json: Health<br> |

# validation

## POST /v1/validate

Validate

Validate receipts against one exact ruleset version, synchronously, and persist the completed run.

Request body (required)

| Content type | Schema |
| --- | --- |
| application/json | ValidationRequest<br> |

| Response | Description / schema |
| --- | --- |
| 201 | Successful Response<br>application/json: RunResponse<br> |
| 403 | Forbidden<br>application/json: ErrorResponse<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 409 | Conflict<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 502 | Bad Gateway<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/runs/{run_id}

Get Run

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| run_id | string | Yes | path<br><br> |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RunResponse<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# runs

## GET /v1/runs

List Runs

Runs, newest first, without their results.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string \| null | No | query<br><br> |
| ruleset_name | string \| null | No | query<br><br> |
| ruleset_version | integer \| null | No | query<br>A version of a ruleset name: give ruleset_name with it<br>A version of a ruleset name: give ruleset_name with it |
| classification_backend | string \| null | No | query<br>As the receipts' provenance reports it<br>As the receipts' provenance reports it |
| execution_mode | string \| null | No | query<br><br> |
| preview | boolean \| null | No | query<br><br> |
| created_from | string (date-time) \| null | No | query<br>Inclusive, with a time zone<br>Inclusive, with a time zone |
| created_to | string (date-time) \| null | No | query<br>Exclusive, with a time zone<br>Exclusive, with a time zone |
| limit | integer | No | query<br><br>default: 20<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: RunPage<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/runs/{run_id}/findings

List Findings

The run's findings in a fixed order, each with the core's record whole. Filters combine; values within one repeat.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| run_id | string | Yes | path<br><br> |
| kind | array<string> \| null | No | query<br>exception, review_signal, open_question or uncertainty; repeat for several<br>exception, review_signal, open_question or uncertainty; repeat for several |
| severity | array<string> \| null | No | query<br>Only exceptions have a severity<br>Only exceptions have a severity |
| rule_id | array<string> \| null | No | query<br><br> |
| receipt_id | array<string> \| null | No | query<br><br> |
| disposition | array<string> \| null | No | query<br>The disposition of the finding's receipt: flag, review or pass<br>The disposition of the finding's receipt: flag, review or pass |
| limit | integer | No | query<br><br>default: 50<br>minimum: 1<br>maximum: 100 |
| offset | integer | No | query<br><br>default: 0<br>minimum: 0 |

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: FindingPage<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/runs/{run_id}/export

Export

The stored run as a file: json is the run with each receipt in the brief's shape; csv is one row per finding; pdf is the full batch report.

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| run_id | string | Yes | path<br><br> |
| format | string | No | query<br>json, csv or pdf<br>json, csv or pdf<br>default: "json" |

| Response | Description / schema |
| --- | --- |
| 200 | The run as a file<br>application/json: any<br><br>text/csv: any<br><br>application/pdf: any<br> |
| 404 | Not Found<br>application/json: ErrorResponse<br> |
| 422 | Unprocessable Entity<br>application/json: ErrorResponse<br> |
| 500 | Internal Server Error<br>application/json: ErrorResponse<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# discovery

## GET /v1/backends

Backends

Each classification backend: installed in the core, enabled here, and available for replay or live use; and the policy compiler's recording sets and live extraction.

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: Backends<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

## GET /v1/taxonomy

Taxonomy

The core's field catalog and category taxonomy, exactly as the validator reads them, with their hashes.

| Response | Description / schema |
| --- | --- |
| 200 | Successful Response<br>application/json: Taxonomy<br> |
| 503 | Service Unavailable<br>application/json: ErrorResponse<br> |

# Component schemas

## ApprovalAction

Move one revision's approval. content_sha256 names the exact text: another revision's, or a stale view's, is a 409.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| action | string | Yes | enum: ["submit", "approve", "return_to_draft"] |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| content_sha256 | string | Yes | pattern: "^[0-9a-f]{64}$" |
| note | string \| null | No |  |

## ApprovalEvent

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| action | string | Yes | enum: ["submit", "approve", "return_to_draft"] |
| from_status | string | Yes | enum: ["draft", "needs_review", "approved"] |
| to_status | string | Yes | enum: ["draft", "needs_review", "approved"] |
| actor | object | Yes |  |
| note | string \| null | Yes |  |
| content_sha256 | string | Yes |  |
| at | string (date-time) | Yes |  |

## ApproveRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S"<br>examples: ["j.doe"] |
| mode | string | No | The publication the approval must authorize. Simulation readings never authorize production.<br>enum: ["production", "simulation"]<br>default: "production" |
| note | string | No | default: ""<br>maxLength: 1000 |
| approval | object \| null | No | An approval record from the core's PolicyCompiler.approve. Without one, the compiler makes it here. |

## Backend

A registered backend, which is an execution adapter (the protocol a model needs), or the LangGraph orchestrator.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| id | string | Yes |  |
| name | string | Yes |  |
| kind | string | Yes | enum: ["adapter", "orchestrator"] |
| legacy_ids | array<string> | No | default: [] |
| provider | string \| null | Yes |  |
| protocol | string \| null | Yes |  |
| installed | boolean | Yes |  |
| enabled | boolean | Yes |  |
| available | boolean | Yes |  |
| default | boolean | Yes |  |
| default_model | string \| null | Yes |  |
| allowed_models | array<string> | Yes |  |
| capabilities | object | Yes |  |
| replay | Replay | Yes |  |
| live | Live | Yes |  |

## Backends

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| default_backend | string | Yes |  |
| execution_modes | object | Yes |  |
| credential_configured | boolean | Yes |  |
| allowed_models | array<string> | Yes |  |
| allowed_classification_models | array<string> | Yes |  |
| allowed_translation_models | array<string> | Yes |  |
| backends | array<Backend> | Yes |  |
| orchestrators | array<Orchestrator> | Yes |  |
| providers | array<Provider> | Yes |  |
| classification_models | array<ClassificationModel> | Yes |  |
| policy_compiler | PolicyCompiler | Yes |  |
| rulesets | RulesetPermissions | Yes |  |
| evaluations | EvaluationPermissions | Yes |  |
| translation | Translation | Yes |  |

## Baseline

This compilation beside the latest published version: what its rules change, and the earlier decisions.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| published | object \| null | Yes |  |
| rule_changes | array<object> | No | default: [] |
| changed_clauses | array<object> | No | default: [] |
| decisions | array<PreviousDecision> | No | default: [] |

## BatchResult

A batch's results, in receipt ID order.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| mode | string | Yes | enum: ["replay", "live", "deterministic"] |
| context_complete | boolean | Yes |  |
| results | array<ReceiptResult> | Yes |  |

## Body_upload_dataset_v1_evaluations_datasets_post

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| file | string | Yes | A .yaml, .yml, .json or .jsonl dataset |
| name | string \| null | No | Required unless the file names the dataset |
| description | string \| null | No |  |
| context_complete | boolean \| null | No | Whether the receipts are all that could matter to a rule spanning receipts; true by default |

## Budget

Independent limits of a live run: each one given applies, and one left out is no limit of that kind.
Best effort: a call's cost is known only once it returns (see EVALUATIONS.md).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| max_spend_usd | number \| string \| null | No | US dollars, as text or an exact number, such as "0.50" |
| max_provider_calls | integer \| null | No |  |
| max_receipts | integer \| null | No | Refuses the run unless the dataset holds at most this many receipts |

## Category

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| id | string | Yes |  |
| restricted | boolean \| null | Yes |  |
| examples | string \| null | Yes |  |

## ChangeRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| change_request_id | string | Yes |  |
| extraction_id | string | Yes |  |
| comparison_id | string | Yes |  |
| rule_id | string | Yes |  |
| field | string | Yes |  |
| policy_doc | string | Yes |  |
| document_id | string | Yes |  |
| requested_correction | string | Yes |  |
| comment | string \| null | Yes |  |
| status | string | Yes | enum: ["open", "resolved"] |
| requested_by | object | Yes |  |
| created_at | string (date-time) | Yes |  |
| resolved_by | object \| null | No |  |
| resolution_note | string \| null | No |  |
| resolved_at | string (date-time) \| null | No |  |

## ChangeRequestCreate

A reviewer's request that one compared field be corrected before the extraction is used. It changes nothing
itself: no document, reading or version is rewritten. While open, no version is made from the extraction, and no
version made from it is approved.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| comparison_id | string | Yes | minLength: 1<br>maxLength: 100<br>pattern: "\\S" |
| requested_correction | string | Yes | minLength: 1<br>maxLength: 2000<br>pattern: "\\S" |
| comment | string \| null | No |  |

## ChangeRequestResolve

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| note | string | Yes | minLength: 1<br>maxLength: 2000<br>pattern: "\\S" |

## ClaimedActor

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| claimed | string | Yes |  |
| verified | boolean | No | const: false<br>default: false |

## ClassificationModel

A model the deployment names for classification, with the one execution adapter its ID needs.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| model | string | Yes |  |
| provider | string | Yes |  |
| backend | string | Yes |  |
| protocol | string | Yes |  |
| default | boolean | Yes |  |
| allowed | boolean | Yes |  |
| sources | array<string> | Yes |  |
| capabilities | object | Yes |  |

## ClassificationSelection

The classification this request chooses. Anything left out comes from the core's config.yaml.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| backend | string \| null | No | openrouter, jev or langgraph, as the deployment allows |
| model | string \| null | No |  |
| translation_model | string \| null | No |  |
| langgraph | object \| null | No | Changes to config.yaml's classification.langgraph, as the core's langgraph_config takes them |
| replay_set | string \| null | No | A named set of recorded answers, replay mode only |

## CompareRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_ids | array<string> | Yes | minItems: 2<br>maxItems: 10 |

## Compilation

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| recording_set | string | Yes |  |
| recorded_with | object \| null | No |  |
| extraction_id | string \| null | No |  |
| prepared_review | PreparedReview \| null | No |  |
| reading_kind | string | Yes | enum: ["model", "simulation"] |
| extraction_sha256 | string | Yes |  |
| output_sha256 | string | Yes |  |
| ready_for_approval | boolean | Yes |  |
| blockers | array<string> | Yes |  |
| comparisons_needing_review | integer | Yes |  |
| documents | array<CompiledDocument> | No | default: [] |
| rule_overrides | array<object> | No | default: [] |

## CompilationAnalytics

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| duration_ms | integer | Yes |  |
| provider_latency_ms | integer | Yes |  |
| live_calls | integer | Yes |  |
| failed_calls | integer | Yes |  |
| reused_documents | integer | Yes |  |
| documents | integer | Yes |  |
| new_spend_usd | string | Yes |  |
| cost_completeness | string | Yes | enum: ["complete", "incomplete"] |
| unpriced_calls | integer | Yes |  |
| input_tokens | integer \| null | Yes |  |
| output_tokens | integer \| null | Yes |  |
| calls | array<CompilationCall> | Yes |  |

## CompilationCall

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| model | string \| null | Yes |  |
| document_sha256 | string | Yes |  |
| latency_ms | integer | Yes |  |
| failed | boolean | Yes |  |
| cost_usd | string \| null | Yes |  |
| input_tokens | integer \| null | Yes |  |
| output_tokens | integer \| null | Yes |  |

## CompileSource

Policy documents for the core's PolicyCompiler, read from one named set of recorded readings.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| documents | array<PolicyDocumentInput> | Yes | minItems: 1<br>maxItems: 50 |
| recording_set | string | Yes | A server-side set of recorded readings; the compiler never calls a model here<br>pattern: "^[A-Za-z0-9_.-]{1,64}$" |

## CompiledDocument

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes |  |
| filename | string | Yes |  |
| sha256 | string | Yes |  |
| revision | integer \| null | No |  |

## CompilerLive

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| permitted | boolean | Yes |  |
| configured | boolean | Yes |  |
| credential_configured | boolean | Yes |  |
| available | boolean | Yes |  |

## CompilerRecordingSet

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| name | string | Yes |  |
| present | boolean | Yes |  |
| live_target | boolean | Yes |  |

## CompilerReplay

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| supported | boolean | Yes |  |
| sets | array<CompilerRecordingSet> | Yes |  |

## Country

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| id | string | Yes |  |
| home_currency | string | Yes |  |

## DatasetDetail

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |
| description | string \| null | Yes |  |
| format | string | Yes |  |
| filename | string | Yes |  |
| schema_version | string | Yes |  |
| context_complete | boolean | Yes |  |
| original_sha256 | string | Yes |  |
| canonical_sha256 | string | Yes |  |
| stats | object | Yes |  |
| created_at | string (date-time) | Yes |  |
| cases | array<object> \| null | No |  |

## DatasetEdit

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| description | string \| null | Yes |  |

## DatasetPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| items | array<DatasetSummary> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## DatasetSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |
| description | string \| null | Yes |  |
| format | string | Yes |  |
| filename | string | Yes |  |
| schema_version | string | Yes |  |
| context_complete | boolean | Yes |  |
| original_sha256 | string | Yes |  |
| canonical_sha256 | string | Yes |  |
| stats | object | Yes |  |
| created_at | string (date-time) | Yes |  |

## DispositionReasonResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| code | string | Yes |  |
| basis | string | Yes |  |
| rule_ids | array<string> | Yes |  |

## DocumentCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| filename | string | Yes | maxLength: 120<br>pattern: "^[A-Za-z0-9][A-Za-z0-9._-]*\\.md$"<br>examples: ["meals-and-entertainment-policy.md"] |
| content | string | Yes | UTF-8 Markdown in the policy-document format, stored exactly as sent |
| title | string \| null | No |  |
| source | DocumentSource | No | default: {"kind": "upload"} |
| category | string \| null | No |  |
| description | string \| null | No |  |
| scope | DocumentScope \| null | No |  |

## DocumentDetail

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes |  |
| revision | integer | Yes |  |
| filename | string | Yes |  |
| title | string \| null | Yes |  |
| content_sha256 | string | Yes |  |
| size_bytes | integer | Yes |  |
| source | DocumentSource | Yes |  |
| based_on_revision | integer \| null | Yes |  |
| created_at | string (date-time) | Yes |  |
| category | string \| null | No |  |
| description | string \| null | No |  |
| scope | DocumentScope \| null | No |  |
| duplicate_of | string \| null | No |  |
| profile_updated_at | string (date-time) \| null | No |  |
| approval | RevisionApprovalState | No | default: {"status": "draft"} |
| content | string | Yes |  |

## DocumentPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| items | array<DocumentSummary> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## DocumentScope

Where a document applies, as given: regions, entities and expense types. Empty is not stated.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| regions | array<string> | No | maxItems: 20 |
| entities | array<string> | No | maxItems: 20 |
| expense_types | array<string> | No | maxItems: 40 |

## DocumentSource

Where the text came from. Recorded as given: provenance, not proof.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| kind | string | No | default: "upload"<br>pattern: "^[a-z][a-z_]{0,39}$" |
| description | string \| null | No |  |
| reference | string \| null | No |  |

## DocumentSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes |  |
| revision | integer | Yes |  |
| filename | string | Yes |  |
| title | string \| null | Yes |  |
| content_sha256 | string | Yes |  |
| size_bytes | integer | Yes |  |
| source | DocumentSource | Yes |  |
| based_on_revision | integer \| null | Yes |  |
| created_at | string (date-time) | Yes |  |
| category | string \| null | No |  |
| description | string \| null | No |  |
| scope | DocumentScope \| null | No |  |
| duplicate_of | string \| null | No |  |
| profile_updated_at | string (date-time) \| null | No |  |
| approval | RevisionApprovalState | No | default: {"status": "draft"} |

## DuplicatePublication

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| rationale | string | Yes | minLength: 1<br>maxLength: 1000<br>pattern: "\\S" |

## Entity

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| id | string | Yes |  |
| countries | array<string> | Yes |  |

## ErrorInfo

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| code | string | Yes |  |
| message | string | Yes |  |
| details | array<any> \| object \| null | No |  |

## ErrorResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| error | ErrorInfo | Yes |  |

## EvaluationCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| dataset_id | string | Yes | One exact, immutable dataset version<br>minLength: 1<br>maxLength: 64 |
| ruleset_id | string | Yes | One exact, immutable ruleset version<br>minLength: 1<br>maxLength: 64 |
| preview | boolean | No | Evaluate against a version not published for production, where the deployment allows previews<br>default: false |
| execution_mode | string | No | replay (the default) reads only recorded answers; live calls paid providers<br>enum: ["replay", "live"]<br>default: "replay" |
| classification | ClassificationSelection | No | default: {} |
| confirm_live | boolean | No | Live only: true to confirm that this run calls paid providers<br>default: false |
| budget | Budget \| null | No | Live only: at least one limit, unless confirm_uncapped |
| confirm_uncapped | boolean | No | Live only: true, with no budget, to confirm a run with no spending, call or receipt limit<br>default: false |
| idempotency_key | string \| null | No | The same key and request return the same evaluation |
| label | string \| null | No |  |

## EvaluationDetail

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes |  |
| label | string \| null | Yes |  |
| status | string | Yes | enum: ["queued", "running", "cancel_requested", "completed", "failed", "cancelled", "budget_exhausted"] |
| dataset | object | Yes |  |
| ruleset | object | Yes |  |
| preview | boolean | Yes |  |
| execution_mode | string | Yes |  |
| configuration | object | Yes |  |
| progress | object | Yes |  |
| created_at | string (date-time) | Yes |  |
| started_at | string (date-time) \| null | Yes |  |
| completed_at | string (date-time) \| null | Yes |  |
| summary | object \| null | Yes |  |
| spend_summary | object | Yes |  |
| stop_reason | string \| null | Yes |  |
| error | object \| null | Yes |  |
| classification | object | Yes |  |
| replay | object \| null | Yes |  |
| live | object \| null | Yes |  |
| budget | object \| null | Yes |  |
| attempts | integer | Yes |  |
| updated_at | string (date-time) | Yes |  |
| cancel_requested_at | string (date-time) \| null | Yes |  |
| lease | object \| null | Yes |  |
| result_context | string \| null | Yes |  |
| context_complete | boolean \| null | Yes |  |
| note | string \| null | Yes |  |
| error_summary | map<string, integer> \| null | Yes |  |
| metrics_schema_version | string \| null | Yes |  |
| spend | object | Yes |  |

## EvaluationEdit

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| label | string \| null | Yes |  |

## EvaluationPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| items | array<EvaluationSummary> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## EvaluationPermissions

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| uncapped_allowed | boolean | Yes |  |

## EvaluationSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes |  |
| label | string \| null | Yes |  |
| status | string | Yes | enum: ["queued", "running", "cancel_requested", "completed", "failed", "cancelled", "budget_exhausted"] |
| dataset | object | Yes |  |
| ruleset | object | Yes |  |
| preview | boolean | Yes |  |
| execution_mode | string | Yes |  |
| configuration | object | Yes |  |
| progress | object | Yes |  |
| created_at | string (date-time) | Yes |  |
| started_at | string (date-time) \| null | Yes |  |
| completed_at | string (date-time) \| null | Yes |  |
| summary | object \| null | Yes |  |
| spend_summary | object | Yes |  |
| stop_reason | string \| null | Yes |  |
| error | object \| null | Yes |  |

## ExceptionResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| rule_id | string | Yes |  |
| policy_doc | string | Yes |  |
| severity | string \| null | Yes |  |
| line_ids | array<string> | Yes |  |
| evidence | string | Yes |  |
| source_quote | string | Yes |  |
| explanation | string | Yes |  |

## ExtractedDocument

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes |  |
| revision | integer | Yes |  |
| filename | string | Yes |  |
| content_sha256 | string | Yes |  |
| reading_error | string \| null | Yes |  |

## ExtractionCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| documents | array<RevisionRef> | No | Explicit immutable revisions. With run_mode, IDs or filenames may instead resolve latest revisions<br>default: []<br>maxItems: 50 |
| run_mode | string \| null | No | full: selected or all latest documents; incremental: merge changed documents into a published base; remove: exclude documents and derived rules from a published base. None preserves the original exact-revision API |
| document_ids | array<string> | No | default: []<br>maxItems: 50 |
| filenames | array<string> | No | default: []<br>maxItems: 50 |
| base_ruleset_id | string \| null | No |  |
| ruleset_name | string \| null | No | Resolve the latest published version of this name; without it incremental/removal uses the latest published version company-wide |
| execution_mode | string | No | replay reads recorded readings; live calls a paid model and must be allowed by the deployment<br>enum: ["replay", "live"]<br>default: "replay" |
| recording_set | string \| null | No | Replay only: the recorded readings to read |
| recorded_with | RecordedWith \| null | No | Replay only: read only readings recorded live with these settings |
| extraction_model | string \| null | No | Live only: the model, from the deployment's allowed models |
| temperature | number \| null | No | Live only. The core checks it |
| timeout_seconds | integer \| null | No | Live only. The core checks it |
| actor | string \| null | No | Who asks for the rule overrides: needed with them |
| rule_overrides | array<RuleChange> | No | Reviewed corrections of the draft rules, such as a param a revised policy changes. The compiler builds and validates them like the rule logic itself; each needs a rationale<br>default: []<br>maxItems: 50 |
| carry_overrides_from | string \| null | No | A published ruleset version whose rule corrections this compilation keeps, each with its own reviewer and rationale. A rule given in rule_overrides replaces that rule's carried correction |

## ExtractionJob

An extraction running in the background: where it is, and, once finished, its extraction or why it failed.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| job_id | string | Yes |  |
| status | string | Yes | enum: ["queued", "running", "succeeded", "failed"] |
| execution_mode | string | Yes | enum: ["replay", "live"] |
| extraction_model | string \| null | Yes |  |
| documents | array<string> | Yes |  |
| progress | ExtractionProgress | Yes |  |
| extraction_id | string \| null | Yes |  |
| error | object \| null | Yes |  |
| created_at | string (date-time) | Yes |  |
| started_at | string (date-time) \| null | Yes |  |
| finished_at | string (date-time) \| null | Yes |  |
| updated_at | string (date-time) | Yes |  |

## ExtractionListItem

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| run | object \| null | No |  |
| extraction_id | string | Yes |  |
| analytics | CompilationAnalytics \| null | No |  |
| status | string | Yes | enum: ["no_candidate", "review_required", "ready_for_approval"] |
| documents | array<ExtractedDocument> | Yes |  |
| provenance | ExtractionProvenance | Yes |  |
| extraction_sha256 | string | Yes |  |
| output_sha256 | string | Yes |  |
| candidate_ruleset_sha256 | string \| null | Yes |  |
| result_sha256 | string | Yes |  |
| ready_for_approval | boolean | Yes |  |
| blockers | array<string> | Yes |  |
| agreement_summary | object | Yes |  |
| comparison_count | integer | Yes |  |
| reviews | array<ReviewSummary> | Yes |  |
| created_at | string (date-time) | Yes |  |
| versions | array<VersionRef> | No | default: [] |
| open_change_requests | integer | No | default: 0 |
| rule_overrides | array<object> | No | default: [] |

## ExtractionPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| items | array<ExtractionListItem> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## ExtractionPlanSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| mode | string | Yes |  |
| base_ruleset_id | string \| null | No |  |
| ruleset_name | string \| null | No |  |
| recording_set | string \| null | No |  |
| documents | array<RevisionRef> | Yes |  |
| affected_rule_ids | array<string> | No | default: [] |
| dependency_rule_ids | array<string> | No | default: [] |
| retained_rule_ids | array<string> | No | default: [] |
| removed_rule_ids | array<string> | No | default: [] |
| changed_document_ids | array<string> | No | default: [] |
| removed_document_ids | array<string> | No | default: [] |
| excluded_document_ids | array<string> | No | default: [] |

## ExtractionProgress

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| stage | string | Yes | reading: the documents, one at a time (live); building: the candidate ruleset<br>enum: ["queued", "reading", "building", "done"] |
| done | integer | Yes | Documents read so far |
| total | integer | Yes |  |
| current | string \| null | No | The document being read |

## ExtractionProvenance

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| execution_mode | string | Yes | enum: ["replay", "live"] |
| recording_set | string | Yes |  |
| recorded_with | RecordedWith \| null | Yes |  |
| reading_kind | string | Yes | enum: ["model", "simulation"] |
| extraction_model | string \| null | Yes |  |
| temperature | number \| null | Yes |  |
| timeout_seconds | integer \| null | Yes |  |
| overrides | array<string> | Yes |  |
| rule_overrides_from | string \| null | No |  |
| prompt_sha256 | string | Yes |  |

## ExtractionSource

A stored extraction of document revisions, with the review that readies it unless it needed none.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_id | string | Yes | pattern: "^ext_[0-9a-f]{24}$" |
| review_id | string \| null | No |  |

## ExtractionSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| run | object \| null | No |  |
| extraction_id | string | Yes |  |
| analytics | CompilationAnalytics \| null | No |  |
| status | string | Yes | enum: ["no_candidate", "review_required", "ready_for_approval"] |
| documents | array<ExtractedDocument> | Yes |  |
| provenance | ExtractionProvenance | Yes |  |
| extraction_sha256 | string | Yes |  |
| output_sha256 | string | Yes |  |
| candidate_ruleset_sha256 | string \| null | Yes |  |
| result_sha256 | string | Yes |  |
| ready_for_approval | boolean | Yes |  |
| blockers | array<string> | Yes |  |
| agreement_summary | object | Yes |  |
| comparison_count | integer | Yes |  |
| reviews | array<ReviewSummary> | Yes |  |
| created_at | string (date-time) | Yes |  |
| versions | array<VersionRef> | No | default: [] |
| open_change_requests | integer | No | default: 0 |
| rule_overrides | array<object> | No | default: [] |
| review_required | array<FieldComparison> | Yes |  |
| rule_titles | map<string, string> | No | default: {} |
| rule_logic | map<string, object> | No | default: {} |
| discrepancies | array<object> | No | default: [] |

## ExtractionVersionCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| name | string \| null | No |  |
| review_id | string \| null | No |  |
| publish | boolean | No | default: false |
| actor | string \| null | No |  |
| note | string | No | default: ""<br>maxLength: 1000 |
| mode | string | No | enum: ["production", "simulation"]<br>default: "production" |

## FieldChange

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| rule_id | string | Yes |  |
| field | string | Yes |  |
| before | any | No |  |
| after | any | No |  |

## FieldComparison

Compare one typed rule field across extractors.

Attributes:
    comparison_id: Identity bound to document text, rule, and field.
    regex_value: Regex reading; rule presence is present or absent.
    agreed_value: Shared value when readings agree.
    evidence: Supporting clause text, if available.
    location: Section and heading line, if available.
    review_required: Whether exceptions_only review requires a decision.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| comparison_id | string | Yes |  |
| document_id | string | Yes |  |
| policy_doc | string | Yes |  |
| rule_id | string | Yes |  |
| field | string | Yes |  |
| status | string | Yes | enum: ["agreed", "disagree", "unconfirmed", "only_regex", "only_llm"] |
| regex_value | any | No |  |
| llm_value | any | No |  |
| agreed_value | any | No |  |
| evidence | string \| null | No |  |
| location | object \| null | No |  |
| clause_hash | string \| null | No |  |
| extraction_sha256 | string | Yes |  |
| review_required | boolean | Yes |  |

## Finding

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| finding_id | string | Yes |  |
| kind | string | Yes | enum: ["exception", "review_signal", "open_question", "uncertainty"] |
| receipt_id | string | Yes |  |
| receipt_disposition | string | Yes | enum: ["flag", "review", "pass"] |
| receipt_decided | boolean | Yes |  |
| rule_id | string \| null | Yes |  |
| severity | string \| null | Yes |  |
| line_ids | array<string> \| null | Yes |  |
| disposition_reasons | array<object> | Yes |  |
| record | object | Yes |  |

## FindingCounts

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| exception | integer | Yes |  |
| review_signal | integer | Yes |  |
| open_question | integer | Yes |  |
| uncertainty | integer | Yes |  |

## FindingPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| run_id | string | Yes |  |
| items | array<Finding> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## HTTPValidationError

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| detail | array<ValidationError> | No |  |

## Health

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| status | string | Yes | enum: ["ok", "degraded"] |
| application | string | Yes | const: "running" |
| database | string | Yes | enum: ["ok", "unavailable"] |
| version | string | Yes |  |

## LineResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| line_id | string | Yes |  |
| description_raw | string | Yes |  |
| category | string \| null | Yes |  |
| restricted | boolean \| null | Yes |  |
| confidence | number \| null | Yes |  |
| english_translation | string \| null | No |  |
| attributes | map<string, string \| boolean> \| null | No |  |
| classification_error | string \| null | No |  |
| classification_details | object \| null | No |  |

## Live

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| permitted | boolean | Yes |  |
| credential_configured | boolean | Yes |  |
| available | boolean | Yes |  |

## Metadata

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| model | string \| null | Yes |  |
| translation_model | string \| null | Yes |  |
| retriever | string | Yes |  |
| prompt_sha256 | string \| null | Yes |  |
| ruleset_version | string | Yes |  |
| replayed | boolean | Yes |  |
| latency_ms | integer \| null | Yes |  |

## OpenQuestionResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| rule_id | string | Yes |  |
| line_ids | array<string> | Yes |  |
| reasons | array<string> | Yes |  |

## Orchestrator

How lines reach a model: direct (the chosen model's adapter classifies every line) or LangGraph (its configured nodes).

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| id | string | Yes |  |
| name | string | Yes |  |
| installed | boolean | Yes |  |
| enabled | boolean | Yes |  |
| available | boolean | Yes |  |
| default | boolean | Yes |  |
| nodes | array<OrchestratorNode> \| null | Yes |  |

## OrchestratorNode

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| name | string | Yes |  |
| backend | string | Yes |  |
| model | string \| null | Yes |  |
| provider | string \| null | Yes |  |
| protocol | string \| null | Yes |  |
| adapter | string \| null | No |  |

## PolicyCompiler

What POST /v1/documents/extractions can use here.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| replay | CompilerReplay | Yes |  |
| live | CompilerLive | Yes |  |
| extraction_models | array<string> | No | default: [] |

## PolicyDocumentInput

One policy document in the existing Markdown format.

Attributes:
    document_id: The caller's identifier, carried into results.
    filename: A bare .md name, with no folder. Rules and approvals refer to documents by it.
    content: The document text.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | maxLength: 200<br>pattern: "\\S" |
| filename | string | Yes | maxLength: 200<br>pattern: "^[A-Za-z0-9][A-Za-z0-9._-]*\\.md$" |
| content | string | Yes | pattern: "\\S" |

## PreparedReview

The extraction review a version was made from: the lifecycle review reuses its decisions unless given others.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| review_id | string | Yes |  |
| policy | string | Yes |  |
| review_sha256 | string | Yes |  |

## PreviousDecision

A field of this compilation beside the same field in the published version, with the decision it was published with.

unchanged: same clause text and the same readings, so the earlier decision answers the same question.
changed: the clause or a reading changed since, so the earlier decision must be made again.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| comparison_id | string | Yes |  |
| state | string | Yes | enum: ["unchanged", "changed"] |
| previous | object | Yes |  |
| decision | object | Yes |  |

## ProfileUpdate

A document's business description, kept apart from its revisions: changing it never touches a revision's text.

Only the fields given are changed; a category of null makes the document unclassified.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| category | string \| null | No |  |
| description | string \| null | No |  |
| scope | DocumentScope \| null | No |  |
| duplicate_of | string \| null | No | Marks this document a duplicate of another with identical content, hidden in the library; null restores it. Nothing is deleted. |

## Provenance

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| mode | string | Yes | enum: ["replay", "live", "deterministic"] |
| context_complete | boolean | Yes |  |
| receipts_in_context | integer | Yes |  |
| ruleset_sha256 | string | Yes |  |
| model_overrides | array<string> | No | default: [] |
| classification_backend | string \| null | No |  |

## Provider

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| id | string | Yes |  |
| name | string | Yes |  |
| adapters | array<string> | Yes |  |
| credential_configured | boolean | Yes |  |

## PublishRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string \| null | No |  |
| approval_reference | string \| null | No | The approval to publish. Another one is a 409. |

## Readiness

Where a version stands on Edit, Resolve, Approve, Publish, as the API judges it now. Publishing still
rebuilds the version and checks its approval: can_publish says only that nothing stands in the way.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| status | string | Yes |  |
| next_step | string | Yes | enum: ["submit", "review", "approve", "publish", "none"] |
| rules_changed | integer | Yes |  |
| review_remaining | integer | Yes |  |
| validation_errors | integer | Yes |  |
| approval | string | Yes | enum: ["not_approved", "approved", "published"] |
| approval_mode | string \| null | Yes |  |
| can_publish | boolean | Yes |  |
| blockers | array<ReadinessBlocker> | Yes |  |

## ReadinessBlocker

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| message | string | Yes |  |
| rule_id | string \| null | No |  |
| comparison_id | string \| null | No |  |

## ReceiptResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| receipt_id | string | Yes |  |
| disposition | string | Yes | enum: ["flag", "review", "pass"] |
| line_items | array<LineResult> | Yes |  |
| exceptions | array<ExceptionResult> | Yes |  |
| review_signals | array<ReviewSignalResult> | Yes |  |
| decided | boolean | Yes |  |
| disposition_reasons | array<DispositionReasonResult> | Yes |  |
| open_questions | array<OpenQuestionResult> | Yes |  |
| uncertainty | array<UncertaintyResult> | Yes |  |
| metadata | Metadata | Yes |  |
| provenance | Provenance | Yes |  |

## RecordedWith

The generation settings readings were recorded live with, to replay exactly those readings.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_model | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| temperature | number | Yes | minimum: 0.0<br>maximum: 2.0 |

## Replay

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| supported | boolean | Yes |  |
| sets | array<ReplaySet> | Yes |  |

## ReplaySet

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| name | string | Yes |  |
| present | boolean | Yes |  |
| answer_files | integer \| null | No |  |
| folder | string \| null | No |  |

## ResultPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| evaluation_id | string | Yes |  |
| items | array<object> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## Review

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| decision | string | Yes | enum: ["accepted", "rejected"] |
| reviewer | ClaimedActor | Yes |  |
| reason | string \| null | Yes |  |
| decided_at | string (date-time) | Yes |  |
| policy | string \| null | Yes |  |
| decisions | array<object> \| null | Yes |  |
| review_sha256 | string \| null | Yes |  |

## ReviewCreate

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| extraction_sha256 | string | Yes | The snapshot this review is of: another is a 409<br>pattern: "^[0-9a-f]{64}$" |
| review_policy | string | No | enum: ["exceptions_only", "all_fields"]<br>default: "exceptions_only" |
| decisions | array<object> | Yes | The core's ReviewDecision records: confirm, accept_regex, accept_llm, override or reject |

## ReviewDetail

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| review_id | string | Yes |  |
| extraction_id | string | Yes |  |
| actor | object | Yes |  |
| extraction_sha256 | string | Yes |  |
| review_sha256 | string | Yes |  |
| candidate_ruleset_sha256 | string \| null | Yes |  |
| policy | string | Yes |  |
| decisions | array<object> | Yes |  |
| ready_for_approval | boolean | Yes |  |
| complete | boolean | Yes |  |
| remaining | array<string> | Yes |  |
| issues | array<ReviewIssue> | Yes |  |
| created_at | string (date-time) | Yes |  |

## ReviewIssue

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| level | string | Yes |  |
| code | string | Yes |  |
| message | string | Yes |  |
| comparison_id | string \| null | Yes |  |

## ReviewRequest

A review decision. Accepting a compiled version needs the compiler's review decisions unless nothing awaits one.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S"<br>examples: ["j.doe"] |
| decision | string | Yes | enum: ["accepted", "rejected"] |
| reason | string \| null | No |  |
| review_policy | string | No | enum: ["exceptions_only", "all_fields"]<br>default: "exceptions_only" |
| decisions | array<object> \| null | No | The core's ReviewDecision records, one per comparison, for this version's extraction |

## ReviewSignalResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| rule_id | string | Yes |  |
| reason | string | Yes |  |

## ReviewSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| review_id | string | Yes |  |
| review_sha256 | string | Yes |  |
| policy | string | Yes |  |
| ready_for_approval | boolean | Yes |  |
| remaining | integer | Yes |  |
| error_issues | integer | Yes |  |
| created_at | string (date-time) | Yes |  |

## RevisionApproval

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| status | string | No | enum: ["draft", "needs_review", "approved"]<br>default: "draft" |
| content_sha256 | string \| null | No |  |
| updated_at | string (date-time) \| null | No |  |
| updated_by | object \| null | No |  |
| document_id | string | Yes |  |
| revision | integer | Yes |  |
| history | array<ApprovalEvent> | No | default: [] |

## RevisionApprovalState

The approval of one exact revision. Every revision starts as a draft: none inherits another's approval.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| status | string | No | enum: ["draft", "needs_review", "approved"]<br>default: "draft" |
| content_sha256 | string \| null | No |  |
| updated_at | string (date-time) \| null | No |  |
| updated_by | object \| null | No |  |

## RevisionCreate

New content for an existing document, which keeps its filename: rules cite documents by filename.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| content | string | Yes |  |
| based_on_revision | integer \| null | No | The revision this one replaces. Any other revision created since is a 409. |
| title | string \| null | No |  |
| source | DocumentSource | No | default: {"kind": "upload"} |

## RevisionRef

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes | pattern: "^doc_[0-9a-f]{24}$" |
| revision | integer | Yes | minimum: 1.0 |

## RuleChange

New values for some of one rule's logic: status, title, outcome, severity, applies_when, select, waived_by_ref or params.
The compiler validates them; anything else is refused.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| rule_id | string | Yes | pattern: "^[A-Z][A-Z0-9]*(?:[-.][A-Z0-9]+)+$" |
| set | object | Yes |  |
| rationale | string \| null | No |  |
| add | boolean | No | A new rule for a clause the documents state and no rule implements: set also gives its scope, source_quote and params<br>default: false |

## RuleEditRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| rationale | string | Yes | Why these rules change: kept in the new version's audit<br>minLength: 1<br>maxLength: 2000<br>pattern: "\\S" |
| changes | array<RuleChange> | Yes | minItems: 1<br>maxItems: 50 |

## RuleEdits

How a version made by editing another differs from it: field by field, with who and why.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| based_on | string | Yes |  |
| based_on_version | integer | Yes |  |
| based_on_artifact_sha256 | string | Yes |  |
| actor | string | Yes |  |
| rationale | string | Yes |  |
| at | string (date-time) | Yes |  |
| changes | array<FieldChange> | Yes |  |

## RuleRemoval

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string | Yes | minLength: 1<br>maxLength: 200<br>pattern: "\\S" |
| rationale | string | Yes | minLength: 1<br>maxLength: 2000<br>pattern: "\\S" |

## RulesetCreate

A new draft version from exactly one source: an extraction, policy documents to compile, or an uploaded artifact.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| name | string | Yes | pattern: "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"<br>examples: ["default"] |
| artifact | object \| null | No | A compiled ruleset in the core's RuleSet schema. Structurally checked only: it can never be approved. |
| compile | CompileSource \| null | No | Policy documents to compile here, from one recording set. |
| extraction | ExtractionSource \| null | No | A reviewed extraction of stored document revisions (POST /v1/documents/extractions). |
| based_on_version | integer \| null | No | The version this one replaces. When given, any other version created since is a 409. |
| source | RulesetSource | No | default: {"kind": "upload"} |

## RulesetDetail

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| superseded_by | SupersedingVersion \| null | No |  |
| ruleset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |
| status | string | Yes | enum: ["draft", "in_review", "approved", "published"] |
| ruleset_version | string | Yes |  |
| schema_version | string | Yes |  |
| artifact_sha256 | string | Yes |  |
| rule_count | integer | Yes |  |
| active_rule_count | integer | Yes |  |
| source | RulesetSource | Yes |  |
| based_on_version | integer \| null | No |  |
| compilation | Compilation \| null | No |  |
| edits | RuleEdits \| null | No |  |
| review | Review \| null | No |  |
| approval_reference | string \| null | No |  |
| approval_mode | string \| null | No |  |
| publication_mode | string \| null | No |  |
| created_at | string (date-time) | Yes |  |
| submitted_at | string (date-time) \| null | No |  |
| approved_at | string (date-time) \| null | No |  |
| published_at | string (date-time) \| null | Yes |  |
| artifact | object | Yes |  |
| transitions | array<Transition> | No | default: [] |

## RulesetPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| items | array<RulesetSummary> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## RulesetPermissions

Which ruleset versions requests may use here.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| preview_allowed | boolean | Yes |  |
| publication_modes | array<string> | Yes |  |

## RulesetSource

Where the content came from. Recorded as given: it is provenance, not proof.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| kind | string | No | default: "upload"<br>pattern: "^[a-z][a-z_]{0,39}$"<br>examples: ["upload", "policy_compiler"] |
| description | string \| null | No |  |
| reference | string \| null | No | examples: ["rules/ruleset.json"] |

## RulesetSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| superseded_by | SupersedingVersion \| null | No |  |
| ruleset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |
| status | string | Yes | enum: ["draft", "in_review", "approved", "published"] |
| ruleset_version | string | Yes |  |
| schema_version | string | Yes |  |
| artifact_sha256 | string | Yes |  |
| rule_count | integer | Yes |  |
| active_rule_count | integer | Yes |  |
| source | RulesetSource | Yes |  |
| based_on_version | integer \| null | No |  |
| compilation | Compilation \| null | No |  |
| edits | RuleEdits \| null | No |  |
| review | Review \| null | No |  |
| approval_reference | string \| null | No |  |
| approval_mode | string \| null | No |  |
| publication_mode | string \| null | No |  |
| created_at | string (date-time) | Yes |  |
| submitted_at | string (date-time) \| null | No |  |
| approved_at | string (date-time) \| null | No |  |
| published_at | string (date-time) \| null | Yes |  |

## RunExecution

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| started_at | string (date-time) | Yes |  |
| completed_at | string (date-time) | Yes |  |
| duration_ms | integer | Yes |  |
| receipt_count | integer | Yes |  |
| core_version | string | Yes |  |
| api_version | string | Yes |  |

## RunPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| items | array<RunSummary> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## RunResponse

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| run_id | string | Yes |  |
| status | string | Yes | const: "completed" |
| ruleset | RunRuleset | Yes |  |
| execution_mode | string | Yes | enum: ["replay", "live", "deterministic"] |
| preview | boolean | No | default: false |
| context_complete | boolean | Yes |  |
| classification | ClassificationSelection | Yes |  |
| results | BatchResult | Yes |  |
| setup | object \| null | No |  |
| execution | RunExecution | Yes |  |
| created_at | string (date-time) | Yes |  |

## RunRuleset

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |
| status | string | Yes |  |
| publication_mode | string \| null | No |  |
| artifact_sha256 | string | Yes |  |
| ruleset_version | string | Yes |  |

## RunSummary

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| run_id | string | Yes |  |
| status | string | Yes | const: "completed" |
| created_at | string (date-time) | Yes |  |
| ruleset | RunRuleset | Yes |  |
| execution_mode | string | Yes | enum: ["replay", "live", "deterministic"] |
| preview | boolean | Yes |  |
| context_complete | boolean | Yes |  |
| classification | ClassificationSelection | Yes |  |
| classification_backend | string \| null | Yes |  |
| setup | object \| null | No |  |
| receipt_count | integer | Yes |  |
| dispositions | map<string, integer> | Yes |  |
| counts | FindingCounts \| null | Yes |  |

## Source

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| path | string | Yes |  |
| sha256 | string | Yes |  |
| input_contract_version | integer \| null | No |  |

## SourceDocument

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| document_id | string | Yes |  |
| revision | integer \| null | Yes |  |
| filename | string | Yes |  |
| content_sha256 | string | Yes |  |
| content | string | Yes |  |

## SourceHighlight

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| line | integer | Yes |  |
| start | integer | Yes |  |
| end | integer | Yes |  |
| text | string | Yes |  |
| side | string | Yes | enum: ["regex", "llm", "clause"] |

## SourceLocation

Where a compared field's source is, verified against the exact revision the compiler read; never approximate.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| comparison_id | string | Yes |  |
| document | SourceDocument | Yes |  |
| revision_verified | boolean | Yes |  |
| clause_verified | boolean | Yes |  |
| location | map<string, integer> \| null | Yes |  |
| section | string \| null | Yes |  |
| heading_line | integer \| null | Yes |  |
| highlights | array<SourceHighlight> | Yes |  |
| reason | string \| null | Yes |  |

## SupersedingVersion

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |

## Taxonomy

Read from the core's rules/fields.yaml and data/category-taxonomy.md. A section the catalog lacks is None.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| catalog | Source | Yes |  |
| taxonomy | Source | Yes |  |
| categories | array<Category> \| null | Yes |  |
| entities | array<Entity> \| null | Yes |  |
| countries | array<Country> \| null | Yes |  |
| expense_types | array<string> \| null | Yes |  |
| currencies | map<string, integer> \| null | Yes |  |
| vocabularies | map<string, array<string>> | Yes |  |
| fields | map<string, map<string, string>> | Yes |  |

## Transition

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| from_status | string \| null | Yes |  |
| to_status | string | Yes | enum: ["draft", "in_review", "approved", "published"] |
| at | string (date-time) | Yes |  |
| actor | ClaimedActor \| null | Yes |  |
| content_hash | string | Yes |  |
| approval_reference | string \| null | Yes |  |
| reason | string \| null | Yes |  |

## TransitionRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| actor | string \| null | No |  |
| reason | string \| null | No |  |

## Translation

The translator: called only for non-English lines, but counted among a live run's models whenever enabled.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| enabled | boolean | Yes |  |
| default_model | string \| null | Yes |  |
| models | array<TranslationModel> | Yes |  |

## TranslationModel

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| model | string | Yes |  |
| provider | string | Yes |  |
| default | boolean | Yes |  |
| allowed | boolean | Yes |  |

## UncertaintyResult

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| line_id | string | Yes |  |
| field | string | Yes |  |
| state | string | Yes |  |
| reason | string | Yes |  |
| confidence | number \| null | Yes |  |

## ValidationError

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| loc | array<string \| integer> | Yes |  |
| msg | string | Yes |  |
| type | string | Yes |  |
| input | any | No |  |
| ctx | object | No |  |

## ValidationRequest

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes | One exact, immutable ruleset version<br>minLength: 1<br>maxLength: 64 |
| receipts | array<object> | Yes | Raw receipt records in the core's format (data/README.md), without labels or notes<br>minItems: 1 |
| context_complete | boolean | No | Whether every receipt that could matter to a cross-receipt rule is included, true by default: submit all relevant receipts in one batch. False leaves totals a missing receipt could change undecided.<br>default: true |
| execution_mode | string | No | replay (the default) answers only from recordings; live calls paid providers and must be allowed by the deployment<br>enum: ["replay", "live", "deterministic"]<br>default: "replay" |
| classification | ClassificationSelection | No | default: {} |
| preview | boolean | No | Validate against a version that is not published for production, such as a draft. Only where the deployment allows previews; the run records it.<br>default: false |

## VersionChanges

A version beside the version published before it: each rule field that differs (a rule only one has is field
"rule"). against is None when no other version was published before it.

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| against | object \| null | Yes |  |
| rule_changes | array<FieldChange> | Yes |  |

## VersionRef

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| ruleset_id | string | Yes |  |
| name | string | Yes |  |
| version | integer | Yes |  |
| status | string | Yes |  |

## expense_api__schemas__documents__ComparisonPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_sha256 | string | Yes |  |
| items | array<FieldComparison> | Yes |  |
| total | integer | Yes |  |
| limit | integer | Yes |  |
| offset | integer | Yes |  |

## expense_api__schemas__rulesets__ComparisonPage

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| extraction_sha256 | string | Yes |  |
| comparisons | array<FieldComparison> | Yes |  |

