export function BackgroundLayer() {
  return (
    <div 
      className="fixed inset-0 -z-10 pointer-events-none"
      data-testid="app-bg"
      aria-hidden="true"
    >
      <div className="absolute inset-0 bg-background" />
      
      {/* Subtle radial glows */}
      <div className="absolute inset-0">
        <div 
          className="absolute top-0 left-1/4 w-[800px] h-[800px] -translate-x-1/2 -translate-y-1/2"
          style={{
            background: 'radial-gradient(circle, rgba(14, 165, 164, 0.04) 0%, transparent 70%)',
          }}
        />
        <div 
          className="absolute bottom-0 right-1/4 w-[700px] h-[700px] translate-x-1/2 translate-y-1/2"
          style={{
            background: 'radial-gradient(circle, rgba(34, 211, 238, 0.03) 0%, transparent 70%)',
          }}
        />
      </div>
      
      {/* SVG texture overlay */}
      <div 
        className="absolute inset-0 opacity-[0.08] dark:opacity-[0.035]"
        style={{
          backgroundImage: 'url(/bg/app-bg.svg)',
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          backgroundRepeat: 'no-repeat',
        }}
      />
    </div>
  )
}
