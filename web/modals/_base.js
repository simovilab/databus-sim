// Shared modal factory. Uses native <dialog> element.
// Each modal module imports { openModal } from './_base.js'.
//
// openModal({
//   title: string,
//   bodyHTML: string,
//   onMount?: (dlg: HTMLDialogElement) => void,
//   onSubmit: (dlg: HTMLDialogElement) => result | null,
// }) → Promise<result | null>
//
// Resolves null on backdrop click, Escape, or Cancel.
// Resolves result on Submit (null return from onSubmit keeps dialog open with validation error).

export function openModal({ title, bodyHTML, onMount, onSubmit }) {
    return new Promise(resolve => {
        const dlg = document.createElement('dialog');
        dlg.className = 'modal';

        dlg.innerHTML = `
            <form class="modal__form" novalidate>
                <header class="modal__header">
                    <h2 class="modal__title">${title}</h2>
                    <button type="button" class="modal__close" aria-label="Close">&times;</button>
                </header>
                <div class="modal__body">${bodyHTML}</div>
                <footer class="modal__footer">
                    <button type="button" class="btn-secondary btn-cancel">Cancel</button>
                    <button type="submit" class="btn-primary">Submit</button>
                </footer>
            </form>
        `;

        document.getElementById('modal-root').appendChild(dlg);

        if (onMount) {
            try { onMount(dlg); } catch (e) { console.error('modal onMount error', e); }
        }

        function close(result) {
            dlg.close();
            dlg.remove();
            resolve(result);
        }

        // Backdrop click
        dlg.addEventListener('click', e => {
            if (e.target === dlg) close(null);
        });

        // Escape key
        dlg.addEventListener('cancel', e => {
            e.preventDefault();
            close(null);
        });

        dlg.querySelector('.modal__close').addEventListener('click', () => close(null));
        dlg.querySelector('.btn-cancel').addEventListener('click',    () => close(null));

        dlg.querySelector('.modal__form').addEventListener('submit', e => {
            e.preventDefault();
            let result;
            try {
                result = onSubmit(dlg);
            } catch (err) {
                console.error('modal onSubmit error', err);
                return;
            }
            if (result !== null && result !== undefined) close(result);
            // null = validation failed, stay open
        });

        dlg.showModal();
        // Focus first interactive element
        const first = dlg.querySelector('input, select, textarea, button');
        if (first) first.focus();
    });
}
