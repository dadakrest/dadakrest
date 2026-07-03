export default function Footer() {
  return (
    <footer className="border-t border-slate-200 bg-slate-50">
      <div className="mx-auto flex max-w-6xl flex-col gap-4 px-6 py-10 text-sm text-slate-500 sm:flex-row sm:items-center sm:justify-between">
        <p>&copy; {new Date().getFullYear()} Nexora Consulting. All rights reserved.</p>
        <p>123 Market Street, Suite 400 · Springfield · hello@nexoraconsulting.com</p>
      </div>
    </footer>
  );
}
