document.addEventListener('DOMContentLoaded', () => {
    
    const contentWrapper = document.querySelector('.content-wrapper');
    const searchBar = document.getElementById('search-bar');

    let dbCursor = 0;
    let isFetching = false;
    let allAnimes = [];

    async function fetchMoreAnimes() {
        if (isFetching) return;
        isFetching = true;

        try {
            const cursorParam = dbCursor ? encodeURIComponent(dbCursor) : 0;
            const response = await fetch(`/animes?cursor=${cursorParam}`);

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Status: ${response.status}\n${errorText}`);
            }

            const data = await response.json();
            dbCursor = data.cursor;

            if (!data.animes) {
                window.removeEventListener('scroll', onScroll);
                return;
            }

            allAnimes.push(...data.animes);
            renderAnimes(data.animes);
        } catch (err) {
            console.error("Fetch error:", err);
        } finally {
            isFetching = false;
        }
    }

    function renderAnimes(animes) {
        for (const anime of animes) {
            const card = document.createElement('div');
            card.className = 'anime-card';

            card.innerHTML = `
                <div class="banner" style="background-image: url('${anime.banner}');"></div>
                <div class="anime-content">
                    <h2>${anime.title}</h2>
                    <div class="metadata">
                        <span>⭐ ${anime.rating ?? 'N/A'}</span>
                        <span>#${anime.mal_ranking ?? 'N/A'}</span>
                        <span>👥 ${anime.members?.toLocaleString() ?? 'N/A'}</span>
                    </div>
                    <div class="anime-genres">${anime.genres.join(', ')}</div>
                    <p class="synopsis">${anime.synopsis || 'No synopsis available.'}</p>
                </div>
            `;

            card.addEventListener('click', () => {
                sessionStorage.setItem('selectedAnime', JSON.stringify(anime));
                window.location.href = `/view/anime/${anime.id}`;
            });            

            contentWrapper.appendChild(card);
        }
    }

    function filterAnimes(keyword) {
        contentWrapper.innerHTML = '';
        const filtered = allAnimes.filter(anime =>
            anime.title.toLowerCase().includes(keyword.toLowerCase()) ||
            anime.genres.some(genre => genre.toLowerCase().includes(keyword.toLowerCase()))
        );
        renderAnimes(filtered);
    }

    function onScroll() {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 400;
        if (nearBottom) fetchMoreAnimes();
    }

    searchBar.addEventListener('input', (e) => {
        const keyword = e.target.value.trim();
        filterAnimes(keyword);
    });

    fetchMoreAnimes();
    window.addEventListener('scroll', onScroll);
});
