import { Component, ErrorInfo, ReactNode } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { AlertTriangle, Copy, RefreshCw } from 'lucide-react'
import { ApiError } from '@/lib/api/errors'
import { getLastRequest } from '@/lib/api/telemetry'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error: Error | null
  errorInfo: ErrorInfo | null
  copied: boolean
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      copied: false,
    }
  }

  static override getDerivedStateFromError(error: Error): Partial<State> {
    return {
      hasError: true,
      error,
    }
  }

  override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('ErrorBoundary caught an error:', error, errorInfo)
    this.setState({
      errorInfo,
    })
  }

  handleReset = () => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
      copied: false,
    })
    window.location.href = '/'
  }

  handleCopyDiagnostics = () => {
    const { error, errorInfo } = this.state
    const lastRequest = getLastRequest()

    const diagnostics = {
      error: {
        name: error?.name,
        message: error?.message,
        stack: error?.stack,
      },
      errorInfo: {
        componentStack: errorInfo?.componentStack,
      },
      apiError: error && 'code' in error ? {
        code: (error as ApiError).code,
        requestId: (error as ApiError).requestId,
        details: (error as ApiError).details,
      } : null,
      lastRequest: lastRequest ? {
        method: lastRequest.method,
        url: lastRequest.url,
        status: lastRequest.status,
        duration: lastRequest.duration,
        requestId: lastRequest.requestId,
        error: lastRequest.error,
      } : null,
      timestamp: new Date().toISOString(),
      userAgent: navigator.userAgent,
      url: window.location.href,
    }

    navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2))
    this.setState({ copied: true })
    setTimeout(() => this.setState({ copied: false }), 2000)
  }

  override render() {
    if (this.state.hasError) {
      const { error } = this.state
      const isApiError = error && 'code' in error

      return (
        <div className="min-h-screen flex items-center justify-center p-4 bg-background">
          <Card className="max-w-2xl w-full">
            <CardHeader>
              <div className="flex items-center gap-3">
                <AlertTriangle className="h-6 w-6 text-destructive" />
                <div>
                  <CardTitle>Something went wrong</CardTitle>
                  <CardDescription>
                    The application encountered an unexpected error
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Error Details */}
              <div className="p-4 rounded-md bg-destructive/10 border border-destructive/20">
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium text-destructive">Error Message</span>
                    {isApiError && (
                      <span className="text-xs font-mono text-muted-foreground">
                        Code: {(error as ApiError).code}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-destructive/90">
                    {error?.message || 'Unknown error'}
                  </p>
                  {isApiError && 'requestId' in error && (error as ApiError).requestId && (
                    <div className="pt-2 border-t border-destructive/20">
                      <span className="text-xs text-muted-foreground">
                        Request ID: <code className="font-mono">{(error as ApiError).requestId}</code>
                      </span>
                    </div>
                  )}
                </div>
              </div>

              {/* Actions */}
              <div className="flex gap-2">
                <Button
                  onClick={this.handleReset}
                  className="flex-1"
                >
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Return to Dashboard
                </Button>
                <Button
                  variant="outline"
                  onClick={this.handleCopyDiagnostics}
                  className="flex-1"
                >
                  <Copy className="h-4 w-4 mr-2" />
                  {this.state.copied ? 'Copied!' : 'Copy Diagnostics'}
                </Button>
              </div>

              {/* Help Text */}
              <div className="text-xs text-muted-foreground text-center pt-2">
                If this problem persists, please copy the diagnostics and contact support
              </div>
            </CardContent>
          </Card>
        </div>
      )
    }

    return this.props.children
  }
}
