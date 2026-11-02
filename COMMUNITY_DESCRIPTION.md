# qpdf Studio Extension — Community Development Branch

We are extending qpdf's proven low-level PDF engine with an open-source
inspection, comparison and standards-based signing workbench. The upstream source stays intact;
the extension uses the pikepdf/libqpdf binding as its initial integration.

## Motivation

A PDF can be structurally sound while its provenance is unverified.
Cryptographic signature integrity, certificate-chain trust, identity of
the signer, and factual authenticity are distinct checks. This project
aims to make these underlying layers inspectable.

## Already implemented

- Indirect-object inventory and JSON export of dictionaries, page resources,
  annotation references and compressed/raw stream hashes and bytes.
- Content-stream drawing/text operator parsing and text-fragment matrices.
- Two-file object-reference and extracted-text comparison.
- PDFium page rendering, non-persistent PNG pixel-difference comparisons and summaries.
- Font and image-resource inventories with supported embedded-image export.
- Local two-document desktop viewer with structural JSON and report export.
- One-click full analysis bundle with structured JSON, decoded streams, PNG
  previews, metadata, glyph coordinates and signature status.
- Desktop visual difference viewer with page-by-page navigation.
- Real standards-based PDF digital signing using a signing key supplied by
  its legitimate owner (PFX/P12 or PEM); local signed PDF output.
- Independent reporting of signature integrity, certificate trust and claimed
  subject/issuer/certificate fingerprint.

## Still in development

Full visual editing, detailed revision-chain reconstruction, certificate
revocation/status services, accurate glyph geometry, OCR, robust handling of
adversarial PDFs, rich native GUI packaging and full native C++ integration.

## Security research and disclosure

This project does **not** claim a confirmed flaw in the cryptography of CFCA,
a bank, or a specific PDF reader. It highlights a possible **verification
workflow risk** when a relying party treats a mathematically valid signature
as proof that a named organization issued a document. Claims about particular
products require repeatable, properly scoped evidence. Please report suspected
vendor vulnerabilities privately before publishing exploit details.

No real banking records, people's financial data or private signing keys will
be published in this repository. All demonstrations use synthetic documents
and clearly labeled demonstration certificates.

## Upstream and licensing

This extension continues from code other people already shared. It does not
replace those projects and has not been accepted into an official qpdf release.
Keep `NOTICE.md` with this directory. The qpdf copyright and the direct
library licenses are listed there.

The qpdf upstream source is available at https://github.com/qpdf/qpdf and
licensed under Apache-2.0. The extension's own source is also under Apache-2.0.
All dependent projects retain their own license terms.
