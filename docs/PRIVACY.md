# Privacy Policy — Metalens (TEMPLATE — not legal advice; have counsel finalize)

_Last updated: {DATE}_

Metalens lets you upload research PDFs, extract structured data from them, verify that data against the source, and optionally publish the **extracted results** (never the PDFs) to a public catalogue.

## What we store, and how it's protected

- **Your uploaded PDFs and their rendered page images** are stored **privately, per user**. Only you can access them: every request is authorization-checked, and files are served via **short-lived, per-user signed URLs** from a private bucket. Other users cannot access your files.
- **Encryption:** files are **encrypted at rest**. Access by our staff is **restricted by policy** and limited to operations, debugging, and abuse handling.
- **We are not zero-knowledge.** Our servers **do** read your PDF while processing it (to render pages and locate evidence). So we can *technically* access file contents (e.g. via backups or operations) even though other users cannot. We therefore promise **"private to you, encrypted at rest, access-controlled"** — **not** "not even we can read it." (A future end-to-end-encrypted mode, where only your browser holds the key, would change this; it is not offered today.)
- **Provider API keys are never stored on our servers.** Keys you enter live only in your browser and are sent per-request to the AI provider you chose. We keep no `api_key` field anywhere.
- **Account data:** email, a bcrypt password hash, and an optional citation name.

## Public vs private (the bright wall)

Uploads are **private by default**. Making a dataset **public** is an explicit, separate action in which you **warrant you have the right to publish** the results. Publishing shares the **extracted records only** — your **source PDFs are never made public**.

## Your rights (GDPR/CCPA)

- **Deletion:** delete any document (removing its records **and** stored PDF + page images) or your **entire account** (Account → Delete account), which removes your documents, their blobs, and your datasets. Deletions propagate to storage; see our retention window for backups.
- **Access/portability:** export your extracted data as JSON/CSV from the workspace.
- **Lawful basis:** performance of our contract with you (providing the service) and legitimate interests; consent for any optional publishing.

## Contacts

- Data protection / privacy requests: **{privacy@yourdomain}**
- Copyright / takedown: see **DMCA.md** → **{dmca@yourdomain}**

If you serve EU/UK users you are a controller/processor under GDPR: maintain this policy, a data-processing agreement, a records-of-processing entry, retention + deletion controls, and a breach-notification process. PDFs may themselves contain personal data — minimize and delete on request.
