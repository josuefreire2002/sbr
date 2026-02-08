// Header con blur al hacer scroll
const header = document.querySelector('.site-header');
const onScroll = () => {
    if (window.scrollY > 10) {
        header.classList.add('is-scrolled');
    } else {
        header.classList.remove('is-scrolled');
    }
};
window.addEventListener('scroll', onScroll);
onScroll();

// Toggle del menú en móviles
const toggle = document.querySelector('.nav-toggle');
const menu = document.getElementById('nav-menu');

if (toggle && menu) {
    toggle.addEventListener('click', () => {
        const open = menu.classList.toggle('is-open');
        toggle.setAttribute('aria-expanded', String(open));
    });

    // Cerrar menú al navegar
    menu.querySelectorAll('a').forEach(a => {
        a.addEventListener('click', () => {
            menu.classList.remove('is-open');
            toggle.setAttribute('aria-expanded', 'false');
        });
    });
}

// Scroll suave con el indicador
document.querySelectorAll('[data-scroll]').forEach(btn => {
    btn.addEventListener('click', (e) => {
        const sel = btn.getAttribute('data-scroll');
        const target = document.querySelector(sel);
        if (target) {
            e.preventDefault();
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    });
});
