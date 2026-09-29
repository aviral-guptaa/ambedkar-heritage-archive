import { Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Home } from './pages/Home'
import { Explore } from './pages/Explore'
import { AiResearch } from './pages/AiResearch'
import { Manuscripts } from './pages/Manuscripts'
import { ManuscriptDetail } from './pages/ManuscriptDetail'
import { Timeline } from './pages/Timeline'
import { Media } from './pages/Media'
import { Digitize } from './pages/Digitize'
import { KnowledgeGraph, NotFoundPage, Stories } from './pages/Placeholders'
import { AdminApp } from './AdminApp'

export function App() {
  return (
    <Routes>
      {/* The archivist area is a separate tree, so no public layout wraps it. */}
      <Route path="/admin/*" element={<AdminApp />} />
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="explore" element={<Explore />} />
        <Route path="ai-research" element={<AiResearch />} />
        <Route path="manuscripts" element={<Manuscripts />} />
        <Route path="digitize" element={<Digitize />} />
        <Route path="manuscripts/:slug" element={<ManuscriptDetail />} />
        <Route path="timeline" element={<Timeline />} />
        <Route path="knowledge-graph" element={<KnowledgeGraph />} />
        <Route path="media" element={<Media />} />
        <Route path="stories" element={<Stories />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  )
}
