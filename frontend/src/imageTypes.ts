export interface ImageUpload { client_id: string; name: string; mime_type: string; base64: string }
export interface DraftImage extends ImageUpload { bytes: number; pendingId?: string }
export interface ImageMetadata { first_frame_only?: boolean; index: number; name: string; mime_type: string; width: number; height: number; bytes: number; image_digest: string; source_revision: string }
export interface PendingImageInput { message_id: string; text: string; kind: string; status: 'queued' | 'selected' | 'retained' | 'uncertain'; text_truncated?: boolean; images: Omit<ImageMetadata,'source_revision'>[] }
export interface ImageLimits { max_images_per_message: number; max_input_bytes: number; max_total_input_bytes: number; max_dimension?: number; max_pixels?: number; max_base64_bytes?: number; max_total_base64_bytes?: number; max_request_bytes?: number; max_in_flight_requests?: number; max_pending_image_messages?: number; max_pending_image_bytes?: number }
export interface ImageConfiguration { capabilities?: Record<string, { image_inputs: boolean }>; image_input_limits?: ImageLimits }
export function wireImages(images: DraftImage[]): ImageUpload[] { return images.map(({ client_id, name, mime_type, base64 }) => ({ client_id, name, mime_type, base64 })); }

export interface FrozenImageRequest { requestId: string; sessionId: string; runId?: string; kind: 'message' | 'steering' | 'follow_up'; text: string; draftText: string; images: DraftImage[]; revision?: number }
