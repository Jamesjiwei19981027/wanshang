export type Application = {
  id: number;
  name: string;
  slug: string;
  summary: string;
  image_url: string;
  recommended_finish?: string;
};

export type UnitSystem = "imperial" | "metric";

export type SpecFieldDefinition = {
  key: string;
  label: string;
  help_text?: string;
  type: "number" | "text" | "choice" | "boolean";
  quantity?: string;
  required?: boolean;
  choices?: string[];
  units?: { code: string; label: string }[];
  default_unit?: Record<UnitSystem, string>;
};

export type Category = {
  id: number;
  name: string;
  slug: string;
  parent?: string | null;
  path?: { name: string; slug: string }[];
  description: string;
  icon: string;
  image_url: string;
  product_count?: number;
  meta_title?: string;
  meta_description?: string;
  rfq_fields?: SpecFieldDefinition[];
};

export type ProductSpec = {
  key: string;
  label: string;
  type: SpecFieldDefinition["type"];
  quantity?: string;
  display: Record<UnitSystem, string>;
  value: string | number | boolean | null;
  unit?: string;
  show_on_card?: boolean;
  filterable?: boolean;
};

export type Product = {
  id: number;
  title: string;
  slug: string;
  model: string;
  sku: string;
  short_description: string;
  description?: string;
  image_url: string;
  specs: ProductSpec[];
  is_customizable: boolean;
  oem_available: boolean;
  made_to_order?: boolean;
  packaging?: string;
  moq?: string;
  lead_time?: string;
  loading_port?: string;
  featured: boolean;
  category: Category;
  applications: Application[];
  images?: { id?: number; image_url: string; alt_text: string; caption?: string; image_type?: string; sort_order?: number }[];
  documents?: { id: number; title: string; document_type: string; version: string; url: string; previewable?: boolean; file_format?: string; file_size?: number | null; updated_at?: string; product_id?: number }[];
  meta_title?: string;
  meta_description?: string;
  updated_at?: string;
};

export type RfqItem = Pick<Product, "id" | "title" | "model" | "sku"> & { category?: string; quantity: number };

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
