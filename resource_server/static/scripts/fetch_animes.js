document.addEventListener('DOMContentLoaded', () => {
    const contentWrapper = document.querySelector('.content-wrapper');
    const searchBar = document.getElementById('search-bar');
    const searchBtn = document.getElementById('search-btn');
    const genreButton = document.getElementById('genres');

    let dbCursor = 0;
    let isFetching = false;
    let allAnimes = [];
    let searchParam = null;

    async function fetchMoreAnimes() {
        if (isFetching) return;
        isFetching = true;

        try {
            const response = await fetch(searchParam && genreButton.value !== '-1' ?
                 `/animes?cursor=${dbCursor}&search=${searchParam}&genre=${genreButton.value}` : 
                 genreButton.value !== "-1" ? 
                 `/animes?cursor=${dbCursor}&genre=${genreButton.value}` : 
                searchParam ? `/animes?cursor=${dbCursor}&search=${searchParam}` : `/animes?cursor=${dbCursor}`);

            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Status: ${response.status}\n${errorText}`);
            }

            const data = await response.json();
            
            if (!data.animes) {
                window.removeEventListener('scroll', onScroll);
                
                if(dbCursor === 0){
                    const messageDiv = document.createElement('div');
                    messageDiv.classList.add('message-div');

                    const messagePara = document.createElement("p");
                    messagePara.innerText = "Your search powers have no effect here...\nNot even the Sharingan could find what you're looking for.\nTry again before the next episode starts!"

                    const animeRandomizerButton = document.createElement('button');
                    animeRandomizerButton.classList.add('btn-primary');
                    animeRandomizerButton.innerText = 'Try a random anime?'
                    animeRandomizerButton.addEventListener('click', () => {
                        window.location.href = '/animes/random'
                    });
                    
                    messageDiv.appendChild(messagePara);
                    messageDiv.appendChild(animeRandomizerButton);
                    contentWrapper.appendChild(messageDiv);
                }

                return;
            }
            dbCursor = data.cursor;

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
                <img class="banner" src = "/static/${anime.banner}"></img>
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

    function onScroll() {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 400;
        if (nearBottom) fetchMoreAnimes();
    }

    searchBtn.addEventListener('click', async () => {
        searchContents = searchBar.value;
        if((!searchContents || searchContents === undefined || searchContents.trim() === '') && genreButton.value === '-1'){
            alert("Maybe enter a valid anime to search? Just a shot in the dark here though, don't let us limit you >:P");
            return;
        }

        searchParam = searchContents.trim();
        dbCursor = 0;
        contentWrapper.innerHTML = '';
        fetchMoreAnimes();
    });

    window.addEventListener('scroll', onScroll);
    fetchMoreAnimes();
});