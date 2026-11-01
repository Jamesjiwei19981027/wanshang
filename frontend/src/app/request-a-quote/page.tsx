import { Suspense } from "react";
import { PageHero } from "@/components/PageHero";
import { RFQForm } from "@/components/RFQForm";
import { RfqSummary } from "@/components/RfqSummary";
import { pageMetadata } from "@/lib/seo";
import { faqJsonLd, serializeJsonLd } from "@/lib/structured-data";

export const metadata = pageMetadata({ title: "Request a quote", description: "Submit a technical RFQ for trailer axle assemblies and components.", path: "/request-a-quote" });

const rfqFaqs = [
  {
    question: "What information should I include in a trailer axle RFQ?",
    answer: "Include intended trailer application, gross weight and load distribution, hub face length, spring centre, bolt pattern, brake type, operating environment, destination market and any drawing or photo. Add preferred model or SKU when one is already selected.",
  },
  {
    question: "Is a published product selection a final engineering approval?",
    answer: "No. Catalogue values are configuration guidance. Final axle selection requires complete trailer geometry, actual loads, wheels and tyres, mounting interfaces, braking requirements and applicable regulations.",
  },
  {
    question: "Can I attach drawings or photos to the RFQ?",
    answer: "Yes. Attach product references, drawings or photos through the RFQ form. Files are stored privately and reviewed by sales and engineering; they are not published on the public catalogue.",
  },
];

export default function RequestQuotePage() {
  const structuredData = serializeJsonLd(faqJsonLd(rfqFaqs));
  return <main>
    <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: structuredData }} />
    <PageHero eyebrow="Technical RFQ" title="Request a quote" description="Tell us what you need, select existing items or attach a drawing. Technical selections are carried into the sales workflow for review." crumb="Request a quote" />
    <section className="shell form-page">
      <div className="form-layout rfq-layout">
        <aside className="rfq-summary rfq-summary-first">
          <h2>Your RFQ list</h2>
          <p>Review selected products and quantities before submitting. You can also send a custom enquiry without choosing a product.</p>
          <RfqSummary />
        </aside>
        <div className="rfq-form-main">
          <Suspense fallback={<p>Loading form…</p>}><RFQForm /></Suspense>
          <section className="content-card" style={{ marginTop: "1.5rem" }}>
            <h2>RFQ questions</h2>
            <div className="faq-list">
              {rfqFaqs.map((faq) => <details key={faq.question}>
                <summary>{faq.question}</summary>
                <p>{faq.answer}</p>
              </details>)}
            </div>
          </section>
        </div>
      </div>
    </section>
  </main>;
}
