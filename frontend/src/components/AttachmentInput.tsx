"use client";

import { useEffect, useRef, useState } from "react";
import { INQUIRY_ATTACHMENT_ACCEPT, validateInquiryAttachments } from "@/lib/inquiry-attachments";

export function AttachmentInput({ id }: { id: string }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState("");
  const [selectedCount, setSelectedCount] = useState(0);

  useEffect(() => {
    const input = inputRef.current;
    const form = input?.form;
    const reset = () => {
      input?.setCustomValidity("");
      setError("");
      setSelectedCount(0);
    };
    form?.addEventListener("reset", reset);
    return () => form?.removeEventListener("reset", reset);
  }, []);

  return <div className="attachment-input">
    <p id={`${id}-help`} className="attachment-help">PDF, CAD drawings or images. Up to 5 files, 10 MB each and 25 MB in total.</p>
    <input
      ref={inputRef}
      id={id}
      name="attachments"
      type="file"
      multiple
      accept={INQUIRY_ATTACHMENT_ACCEPT}
      aria-invalid={Boolean(error)}
      aria-describedby={`${id}-help${error ? ` ${id}-error` : ""}`}
      onChange={(event) => {
        const files = event.currentTarget.files ?? [];
        const nextError = validateInquiryAttachments(files);
        event.currentTarget.setCustomValidity(nextError);
        setError(nextError);
        setSelectedCount(files.length);
      }}
      onInvalid={(event) => setError(event.currentTarget.validationMessage)}
    />
    {error
      ? <p id={`${id}-error`} className="field-error" role="alert">{error}</p>
      : selectedCount > 0 && <p className="attachment-selection" role="status">{selectedCount} file{selectedCount === 1 ? "" : "s"} selected.</p>}
  </div>;
}
