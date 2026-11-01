export type Application = {
  id: number;
  name: string;
  slug: string;
  summary: string;
  image_url: string;
  recommended_finish?: string;
};

export type Category = {
  id: number;
  name: string;
  slug: string;
  description: string;
  icon: string;
  image_url: string;
  product_count?: number;
  meta_title?: string;
  meta_description?: string;
};

export type AxleSpec = Record<string, string>;

export type Product = {
  id: number;
  title: string;
  slug: string;
  model: string;
  sku: string;
  short_description: string;
  description?: string;
  image_url: string;
  rated_capacity: number | null;
  capacity_unit?: string;
  dimension_unit?: string;
  axle_type: string;
  brake_type: string;
  bolt_pattern: string;
  tube_size: string;
  hub_face_length: string;
  spring_center: string;
  surface_finish: string;
  is_customizable: boolean;
  oem_available: boolean;
  made_to_order?: boolean;
  stud_size?: string;
  grease_cap_type?: string;
  packaging?: string;
  featured: boolean;
  category: Category;
  applications: Application[];
  axle_spec?: AxleSpec;
  images?: { id?: number; image_url: string; alt_text: string; caption?: string; image_type?: string; sort_order?: number }[];
  documents?: { id: number; title: string; document_type: string; version: string; url: string; previewable?: boolean; file_format?: string; file_size?: number | null; updated_at?: string; product_id?: number }[];
  meta_title?: string;
  meta_description?: string;
  updated_at?: string;
};

export type RfqItem = Pick<Product, "id" | "title" | "model" | "sku" | "rated_capacity" | "capacity_unit" | "brake_type"> & { quantity: number };

export type ProductDocument = {
  id: number;
  title: string;
  document_type: string;
  version: string;
  product_title: string;
  product_model: string;
  product_id?: number;
  url: string;
  previewable?: boolean;
  file_format?: string;
  file_size?: number | null;
  updated_at?: string;
};
