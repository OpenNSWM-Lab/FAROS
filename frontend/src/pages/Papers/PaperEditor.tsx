import { useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { AppPageLayout } from '@/components/layout/AppPageLayout'
import { H2, H3, Subtitle } from '@/components/typography/Typography'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { ArrowLeft, Download, FileCheck, ChevronRight, FileText, Copy, CheckCircle2 } from 'lucide-react'
import { usePaper } from '@/lib/hooks/useApi'
import type { PaperSection } from '@/lib/types'
import { paperDisplayStatusClass, paperDisplayStatusLabel } from './paperStatus'

export function PaperEditor() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data: paper, isLoading, error } = usePaper(id!)
  const [activeTab, setActiveTab] = useState('sections')
  const [selectedSection, setSelectedSection] = useState<PaperSection | null>(null)
  const [showToast, setShowToast] = useState(false)

  const handleCite = () => {
    const bibtex = `@article{${paper?.id},
  title={${paper?.title}},
  author={${paper?.authors.join(' and ')}},
  year={2026}
}`
    navigator.clipboard.writeText(bibtex)
    setShowToast(true)
    setTimeout(() => setShowToast(false), 2000)
  }

  if (isLoading) {
    return (
      <AppPageLayout title="Loading..." subtitle="Fetching paper details">
        <Skeleton className="h-96 w-full" />
      </AppPageLayout>
    )
  }

  if (error || !paper) {
    return (
      <AppPageLayout title="Paper Not Found" subtitle="Unable to load paper details">
        <Card>
          <CardContent className="py-8">
            <p className="text-center text-destructive">
              {error?.message || 'Paper not found'}
            </p>
          </CardContent>
        </Card>
      </AppPageLayout>
    )
  }

  return (
    <AppPageLayout
      title={paper.title}
      subtitle={`${paper.authors.join(', ')} • Run: ${paper.runId}`}
      breadcrumb={
        <button onClick={() => navigate('/papers')} className="flex items-center gap-2 text-teal-600 hover:text-teal-700">
          <ArrowLeft className="h-4 w-4" />
          Back to Papers
        </button>
      }
      actions={
        <div className="flex items-center gap-2">
          <Badge variant="outline" className={paperDisplayStatusClass(paper)}>{paperDisplayStatusLabel(paper)}</Badge>
          <Button
            variant="outline"
            size="sm"
            onClick={() => window.open(`/api/v1/papers/${encodeURIComponent(paper.id)}/pdf`, '_blank')}
          >
            <FileText className="mr-2 h-4 w-4" />
            View PDF
          </Button>
          <Button variant="outline" size="sm" onClick={handleCite}>
            <Copy className="mr-2 h-4 w-4" />
            Cite
          </Button>
          <Link to="/review/consistency">
            <Button variant="outline" size="sm">
              <FileCheck className="mr-2 h-4 w-4" />
              Check
            </Button>
          </Link>
        </div>
      }
    >

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="sections">Sections</TabsTrigger>
          <TabsTrigger value="preview">Preview</TabsTrigger>
          <TabsTrigger value="export">Export</TabsTrigger>
        </TabsList>

        <TabsContent value="sections" className="mt-6">
          <div className="grid grid-cols-12 gap-6">
            {/* Left: Section Navigator */}
            <div className="col-span-3">
              <Card>
                <CardHeader>
                  <CardTitle className="text-sm">Sections</CardTitle>
                  <CardDescription className="text-xs">
                    {paper.sections.length} sections
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-1">
                  {paper.sections.map((section) => (
                    <button
                      key={section.title}
                      onClick={() => setSelectedSection(section)}
                      className={`w-full text-left px-3 py-2 rounded-md text-sm transition-colors ${selectedSection?.title === section.title
                        ? 'bg-primary text-primary-foreground'
                        : 'hover:bg-muted'
                        }`}
                    >
                      <div className="flex items-center gap-2">
                        <ChevronRight className="h-3 w-3" />
                        <span className="truncate">{section.title}</span>
                      </div>
                    </button>
                  ))}
                </CardContent>
              </Card>
            </div>

            {/* Right: Section Editor */}
            <div className="col-span-9">
              <Card>
                <CardHeader>
                  <CardTitle>
                    {selectedSection ? selectedSection.title : 'Select a section'}
                  </CardTitle>
                  <CardDescription>
                    {selectedSection ? 'Edit section content' : 'Choose a section from the left to edit'}
                  </CardDescription>
                </CardHeader>
                <CardContent>
                  {selectedSection ? (
                    <div className="space-y-4">
                      <textarea
                        className="w-full h-96 p-4 rounded-md border border-input bg-background font-mono text-sm"
                        value={selectedSection.content}
                        readOnly
                        placeholder="Section content..."
                      />
                      <div className="text-xs text-muted-foreground">
                        Read-only in mock mode. Backend integration required for editing.
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center justify-center h-96 text-sm text-muted-foreground">
                      Select a section to edit
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="preview">
          <Card>
            <CardHeader>
              <CardTitle>Preview</CardTitle>
              <CardDescription>Rendered paper preview</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              <div className="border-b pb-6 mb-6">
                <H2 className="mb-3">{paper.title}</H2>
                <Subtitle>
                  {paper.authors.join(', ')}
                </Subtitle>
              </div>

              {paper.sections.map((section, index) => (
                <div key={index} className="space-y-3 mb-8">
                  <H3>{section.title}</H3>
                  <div className="text-sm leading-relaxed whitespace-pre-wrap bg-muted/30 p-5 rounded-md text-slate-700">
                    {section.content}
                  </div>
                </div>
              ))}

              {paper.bibliography && (
                <div className="space-y-3 border-t pt-6">
                  <H3>References</H3>
                  <div className="text-sm leading-relaxed whitespace-pre-wrap bg-muted/30 p-5 rounded-md font-mono text-xs text-slate-700">
                    {paper.bibliography}
                  </div>
                </div>
              )}

              <div className="text-xs text-muted-foreground">
                Note: Math rendering (KaTeX) not included in this mock. Full LaTeX compilation requires backend.
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="export">
          <Card>
            <CardHeader>
              <CardTitle>Export Options</CardTitle>
              <CardDescription>Download paper in various formats</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-4 md:grid-cols-3">
                <Button variant="outline" disabled className="justify-start">
                  <Download className="mr-2 h-4 w-4" />
                  Export PDF
                </Button>
                <Button variant="outline" disabled className="justify-start">
                  <Download className="mr-2 h-4 w-4" />
                  Export LaTeX
                </Button>
                <Button variant="outline" disabled className="justify-start">
                  <Download className="mr-2 h-4 w-4" />
                  Export DOCX
                </Button>
              </div>
              <div className="text-sm text-muted-foreground bg-muted/50 p-4 rounded-md">
                <strong>Backend not connected:</strong> Export functionality requires backend LaTeX compilation service.
                In production, this would generate properly formatted academic papers with citations, figures, and equations.
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {showToast && (
        <div className="fixed top-4 right-4 z-50 bg-primary text-primary-foreground px-4 py-3 rounded-md shadow-lg flex items-center gap-2 animate-in slide-in-from-top">
          <CheckCircle2 className="h-4 w-4" />
          <span className="text-sm font-medium">BibTeX citation copied to clipboard!</span>
        </div>
      )}
    </AppPageLayout>
  )
}
