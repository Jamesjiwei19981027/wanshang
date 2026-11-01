export type ApplicationGuidance = {
  structure: string;
  loadRange: string;
  axleDirection: string;
  capacityDirection: string;
  brakeDirection: string;
  boltDirection: string;
  finishDirection: string;
  considerations: string[];
  faqs: [string, string][];
};

const commonFaqs: [string, string][] = [
  ["Is this a final axle selection?", "No. The complete trailer geometry, actual loads, wheels, tyres, brakes, interfaces, regulations and safety factors require technical confirmation."],
  ["What should be included in an RFQ?", "Include application, gross and distributed loads, axle count, critical dimensions, wheel and brake details, environment and a marked drawing where available."],
];

const guidance: Record<string, ApplicationGuidance> = {
  "utility-trailers": {
    structure: "Single or tandem layouts with leaf-spring or other approved suspension arrangements.",
    loadRange: "Light to medium duty; calculate from the real gross trailer weight and load distribution.",
    axleDirection: "Straight or drop axle platforms, subject to ride height and clearance.",
    capacityDirection: "Confirm the required capacity per axle and the rating of every connected running-gear component.",
    brakeDirection: "Idler or electric/hydraulic brake direction depends on mass, controls and destination rules.",
    boltDirection: "Common 5- or 6-lug directions; verify wheel PCD, stud and hub compatibility.",
    finishDirection: "Match coating to road exposure, storage and service conditions.",
    considerations: ["Deck height and ground clearance", "Variable cargo position", "Tandem equalisation", "Brake-controller compatibility"],
    faqs: commonFaqs,
  },
  "boat-trailers": {
    structure: "Immersion-prone single or tandem layouts with corrosion-sensitive hubs, brakes and fasteners.",
    loadRange: "Use boat, fuel, equipment and trailer mass plus the actual load distribution.",
    axleDirection: "Straight or torsion direction may be considered after geometry, launching depth and service needs are known.",
    capacityDirection: "Confirm per-axle load with the complete wet operating mass and wheel/tyre ratings.",
    brakeDirection: "Hydraulic drum or disc systems are common directions; verify actuation, flushing and local requirements.",
    boltDirection: "Confirm wheel PCD, compatible coated components, bearings, seals and water-protection arrangement.",
    finishDirection: "Hot-dip galvanising or another verified marine-oriented system may be considered for the full assembly.",
    considerations: ["Freshwater and saltwater exposure must be distinguished", "Rinse and inspect after immersion", "Use compatible corrosion-resistant fasteners", "Maintain seals, grease and bearing protection", "Check drain paths and trapped water", "Define brake flushing and service intervals"],
    faqs: [
      ["Does galvanising make an axle maintenance-free?", "No. Coating damage, dissimilar materials, bearings, seals, brakes and fasteners still require inspection, rinsing and maintenance appropriate to the water exposure."],
      ["What changes for saltwater use?", "Saltwater increases corrosion risk. State immersion frequency, materials, coating system, brake type and maintenance plan for a configuration-specific review."],
      ...commonFaqs,
    ],
  },
  "agricultural-trailers": {
    structure: "Single, tandem or multi-axle layouts exposed to uneven ground, debris and variable loading.",
    loadRange: "Determine from actual payload, dynamic terrain loads and distribution rather than nominal cargo alone.",
    axleDirection: "Heavy straight or application-specific configurations with suitable suspension interfaces.",
    capacityDirection: "Allow technical review of dynamic duty, speed, terrain and safety factor.",
    brakeDirection: "Hydraulic or other system direction depends on tow vehicle, duty and market requirements.",
    boltDirection: "Confirm wheel, tyre, hub, stud and bearing system for the intended terrain and loads.",
    finishDirection: "Select protection for mud, fertiliser, chemicals, washdown and outdoor storage.",
    considerations: ["Shock and side loading", "Mud and crop debris", "Low-speed heavy duty cycles", "Inspection access and field service"],
    faqs: commonFaqs,
  },
  "car-haulers": {
    structure: "Low-deck single or tandem arrangements with loading-angle and clearance constraints.",
    loadRange: "Include vehicle mass, trailer equipment and the actual fore-aft load position.",
    axleDirection: "Straight or drop axle direction depending on deck height, tyre clearance and suspension geometry.",
    capacityDirection: "Confirm combined axle capacity, individual axle loading and all running-gear ratings.",
    brakeDirection: "Braked axles normally require system-level confirmation for the target market.",
    boltDirection: "Common 5-, 6- or 8-lug directions; verify the complete wheel/hub interface.",
    finishDirection: "Road-duty coating selected for climate, road salt and storage.",
    considerations: ["Loading ramp angle", "Dynamic load transfer", "Tie-down configuration", "Tandem brake balance"],
    faqs: commonFaqs,
  },
  "rv-caravan": {
    structure: "Single or tandem layouts where ride, packaging, serviceability and long-distance duty matter.",
    loadRange: "Use actual travelling mass, tanks, equipment and distributed payload.",
    axleDirection: "Straight, drop or torsion direction after floor height, suspension and service needs are known.",
    capacityDirection: "Confirm per-axle and combined system ratings with realistic loaded conditions.",
    brakeDirection: "Electric or other approved braking direction subject to tow controls and destination requirements.",
    boltDirection: "Match wheels, tyres, spares, hubs, bearings and service availability.",
    finishDirection: "Road and climate appropriate coating with maintainable components.",
    considerations: ["Ride and vibration", "Long-distance thermal duty", "Service part availability", "Ground and body clearance"],
    faqs: commonFaqs,
  },
  "construction-trailers": {
    structure: "Robust single or tandem layouts for tools, plant and repeated jobsite loading.",
    loadRange: "Account for concentrated payloads, uneven approaches and frequent loading cycles.",
    axleDirection: "Straight or heavy-duty direction after deck, clearance and duty review.",
    capacityDirection: "Review load concentration, dynamic duty and total running-gear ratings.",
    brakeDirection: "Select and validate brakes as a complete system for mass and market.",
    boltDirection: "Confirm heavy-duty wheel, hub, stud, bearing and tyre compatibility.",
    finishDirection: "Coating and protection for road debris, mud, outdoor storage and abrasion.",
    considerations: ["Concentrated equipment loads", "Jobsite shock loading", "Frequent ramp loading", "Damage inspection access"],
    faqs: commonFaqs,
  },
  "heavy-duty-transport": {
    structure: "Multi-axle or engineered layouts requiring vehicle-level load and interface analysis.",
    loadRange: "Project-specific; static capacity alone is insufficient for heavy-duty duty cycles.",
    axleDirection: "Engineered straight, drop or specialist platform after full geometry review.",
    capacityDirection: "Validate real axle loads, distribution, dynamic factors and legal limits.",
    brakeDirection: "System engineering is required for brakes, actuation, thermal duty and compliance.",
    boltDirection: "Heavy-duty wheel-end selection requires complete hub, bearing, stud, wheel and tyre data.",
    finishDirection: "Specify from environment, maintenance regime, project life and verified test evidence.",
    considerations: ["Vehicle-level engineering", "Fatigue and thermal duty", "Route and regulatory constraints", "Traceable approval drawings"],
    faqs: commonFaqs,
  },
};

export function getApplicationGuidance(slug: string, recommendedFinish?: string): ApplicationGuidance {
  return guidance[slug] ?? {
    structure: "Confirm trailer layout, suspension interfaces and axle count.",
    loadRange: "Calculate from actual gross mass and load distribution.",
    axleDirection: "Select only after geometry and operating requirements are known.",
    capacityDirection: "Confirm capacity per axle and every connected component rating.",
    brakeDirection: "Review the complete brake system and destination requirements.",
    boltDirection: "Confirm stud count, PCD, wheel, hub and bearing compatibility.",
    finishDirection: recommendedFinish || "Match the verified finish to the operating environment.",
    considerations: ["Complete trailer geometry", "Actual loads and distribution", "Wheels, tyres and brakes", "Operating environment and regulations"],
    faqs: commonFaqs,
  };
}
