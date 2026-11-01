import Link from "next/link";
import { PageHero } from "@/components/PageHero";
import { pageMetadata } from "@/lib/seo";
import { getFaqs } from "@/lib/api";
import { faqJsonLd, serializeJsonLd } from "@/lib/structured-data";

export const metadata = pageMetadata({
  title: "FAQ",
  description: "Answers about trailer axle selection, published specifications, RFQ preparation and document requests.",
  path: "/faq",
});

export const revalidate = 60;

export default async function FaqPage() {
  // Questions and answers are managed in the admin; the assistant uses the same list.
  const faqs = await getFaqs() ?? [];
  const faqSections: { title: string; items: { question: string; answer: string }[] }[] = [];
  for (const faq of faqs) {
    const title = faq.group || "Questions";
    let section = faqSections.find((item) => item.title === title);
    if (!section) faqSections.push(section = { title, items: [] });
    section.items.push(faq);
  }
  const allFaqs = faqSections.flatMap((section) => section.items);
  const structuredData = serializeJsonLd(faqJsonLd(allFaqs));
  return <main>
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} />
    <PageHero
      eyebrow="Technical FAQ"
      title="Trailer axle selection and RFQ questions"
      description="Direct answers for buyers comparing axle platforms and preparing a configuration-led enquiry."
      crumb="FAQ"
    />
    <section className="section">
      <div className="shell">
        {faqSections.map((section) => (
          <div key={section.title} style={{ marginBottom: "2.5rem" }}>
            <h2>{section.title}</h2>
            <div className="faq-list">
              {section.items.map((faq) => (
                <details key={faq.question} open>
                  <summary>{faq.question}</summary>
                  <p>{faq.answer}</p>
                </details>
              ))}
            </div>
          </div>
        ))}
        <div className="content-card">
          <h2>Need a configuration review?</h2>
          <p>Send hub face, spring centre, capacity, brake requirement and destination market. Attach a drawing when available.</p>
          <div className="button-row">
            <Link className="button" href="/request-a-quote">Request a quote</Link>
            <Link className="button-outline" href="/contact">Contact sales</Link>
          </div>
        </div>
      </div>
    </section>
  </main>;
}
