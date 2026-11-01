import { DownloadsBrowser } from "@/components/DownloadsBrowser";
import { PageHero } from "@/components/PageHero";
import { getDocuments } from "@/lib/api";
import { pageMetadata } from "@/lib/seo";

export const metadata = pageMetadata({ title: "Download centre", description: "Product catalogs, technical drawings and installation documents.", path: "/downloads" });
export const dynamic = "force-dynamic";

export default async function DownloadsPage() {
  const documents = await getDocuments();
  return <main>
    <PageHero eyebrow="Documents" title="Technical download centre" description="Current product catalogues, drawings, installation information and versioned documents." crumb="Downloads" />
    <section className="shell form-page"><DownloadsBrowser documents={documents} /></section>
  </main>;
}
