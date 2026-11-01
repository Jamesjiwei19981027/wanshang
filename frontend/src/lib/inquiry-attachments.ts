export const INQUIRY_ATTACHMENT_ACCEPT = ".pdf,.dwg,.dxf,.step,.stp,.png,.jpg,.jpeg,.webp";
export const MAX_INQUIRY_ATTACHMENTS = 5;
export const MAX_INQUIRY_ATTACHMENT_BYTES = 10 * 1024 * 1024;
export const MAX_INQUIRY_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024;

type Attachment = Pick<File, "name" | "size">;

export function validateInquiryAttachments(files: Iterable<Attachment>): string {
  const attachments = Array.from(files);
  if (attachments.length > MAX_INQUIRY_ATTACHMENTS) {
    return `Choose no more than ${MAX_INQUIRY_ATTACHMENTS} files.`;
  }
  const oversized = attachments.find((file) => file.size > MAX_INQUIRY_ATTACHMENT_BYTES);
  if (oversized) return `${oversized.name} exceeds the 10 MB per-file limit.`;
  if (attachments.reduce((total, file) => total + file.size, 0) > MAX_INQUIRY_TOTAL_ATTACHMENT_BYTES) {
    return "Combined attachments must not exceed 25 MB.";
  }
  return "";
}
