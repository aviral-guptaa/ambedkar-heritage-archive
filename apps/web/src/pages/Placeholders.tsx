import { NotAvailable, NotFound } from '../components/ui'

/**
 * Knowledge Graph and Stories are part of the required public navigation but are
 * not implemented in this phase. They are listed in the navigation and explain
 * themselves here, rather than being hidden until they exist.
 */
export function KnowledgeGraph() {
  return (
    <NotAvailable
      title="Knowledge Graph"
      reason="The graph data is built and searchable through the API, but the visualisation is not finished in this release."
      whatItWillDo="An explorable network of people, topics, events, places and documents, drawn from the relationships extracted during indexing. Every edge will be shown with its extraction confidence and its review state, and no edge will be presented as established history until an archivist has confirmed it."
    />
  )
}

export function Stories() {
  return (
    <NotAvailable
      title="Stories"
      reason="Curated narrative walkthroughs are not built in this release. Nothing is published here yet."
      whatItWillDo="Guided paths through the archive built from the records themselves — for example caste and the Constitution, or the 1956 Buddhist turn. Every claim in a story will cite the record it rests on and will carry that record's verification status, so a narrative can never be more certain than its evidence."
    />
  )
}

export function NotFoundPage() {
  return <NotFound />
}
