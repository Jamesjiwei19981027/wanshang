import Link from "next/link";
import { PageHero } from "@/components/PageHero";
import { pageMetadata } from "@/lib/seo";
import { faqJsonLd, serializeJsonLd } from "@/lib/structured-data";

export const metadata = pageMetadata({
  title: "FAQ",
  description: "Answers about trailer axle selection, published specifications, RFQ preparation and document requests.",
  path: "/faq",
});

const faqSections: { title: string; items: { question: string; answer: string }[] }[] = [
  {
    title: "Product selection",
    items: [
      {
        question: "How do I choose a trailer axle from this catalogue?",
        answer: "Start with the trailer application and actual gross weight, then filter by rated capacity, axle direction (straight or drop), brake type and bolt pattern. Compare hub face and spring centre on product pages. Submit the complete dimension set through RFQ so engineering can confirm the configuration against your trailer geometry.",
      },
      {
        question: "Are published dimensions a final engineering approval?",
        answer: "No. Catalogue values are published configuration guidance. Final axle selection requires complete trailer geometry, actual load distribution, wheels and tyres, mounting interfaces, braking requirements, operating environment and destination-market regulations.",
      },
      {
        question: "What do hub face and spring centre mean?",
        answer: "Hub face is the distance between the wheel-mounting surfaces of the hubs on opposite ends of the axle. Spring centre is the distance between the centre lines of the two spring seats. Confirm both measurements against the trailer frame, suspension, wheels and required clearances. If either differs from a listed platform, request a configuration review before order release.",
      },
    ],
  },
  {
    title: "RFQ and orders",
    items: [
      {
        question: "What should I include in an RFQ?",
        answer: "Include intended application, gross weight and load distribution, hub face, spring centre, bolt pattern, stud size, brake type, surface finish, destination market and quantity. Attach drawings or photos when available. Add model or SKU when one is already selected.",
      },
      {
        question: "Can I request a custom or OEM configuration?",
        answer: "Yes. Mark custom requirements in the RFQ and provide target dimensions or a drawing. Custom, made-to-order and OEM options depend on the platform, volume and technical review; they are not confirmed by the public catalogue alone.",
      },
      {
        question: "Are technical drawings available?",
        answer: "Published documents appear on product pages and the download centre when released. Controlled or configuration-specific approval drawings are issued through the RFQ workflow after technical review.",
      },
    ],
  },
  {
    title: "Quality, packing and delivery",
    items: [
      {
        question: "How are quality and inspection claims handled?",
        answer: "The site publishes only company-verified test and certification evidence. Final inspection points, traceability, packaging, lead time and loading port are confirmed against the approved configuration and order.",
      },
      {
        question: "Which markets and regulations apply?",
        answer: "Requirements differ by destination market. State the destination and any applicable brake, lighting or marking rules in the RFQ so the configuration can be reviewed for that market.",
      },
    ],
  },
];

export default function FaqPage() {
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
