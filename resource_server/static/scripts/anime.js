document.addEventListener('DOMContentLoaded', async () => {
    let dbCursor = 0;
    let loading = false;
    const pathParts = window.location.pathname.split('/');
    const animeId = pathParts[pathParts.length - 1];
    
    let selectedAnime = null;
    let cachedAnime = null;
    const stored = sessionStorage.getItem('selectedAnime');
    if (stored && stored !== 'undefined' && stored !== '') {
        try {
            cachedAnime = JSON.parse(stored);
        } catch (e) {
            console.warn("Invalid cached anime JSON, clearing...");
            sessionStorage.removeItem('selectedAnime');
        }
    }

    if (cachedAnime && cachedAnime.id == animeId) {
        selectedAnime = cachedAnime;
    } else {
        try {
            const response = await fetch(`/animes/${animeId}`);
            if (!response.ok) throw new Error(await response.text());
            selectedAnime = (await response.json()).anime;
            sessionStorage.setItem('selectedAnime', JSON.stringify(selectedAnime));
        } catch (err) {
            console.error("Fetch error:", err);
            return;
        }
    }

    // Set banner
    const bannerEl = document.getElementById('anime-banner');
    if (bannerEl) {
        bannerEl.src = `/static/${selectedAnime.banner}`;
    }
    const description = document.getElementById('anime-description');
    console.log(selectedAnime)
    description.innerHTML = `<h1>${selectedAnime.title}</h1> <hr>` + selectedAnime.synopsis || "Failed to fetch this anime's synopsis"

    // Populate stats and links
    function populateStats(anime) {
        const statsEl = document.getElementById('anime-stats');
        if (!statsEl) return;
        statsEl.innerHTML = `
            <h2>${anime.title}</h2>     
            <div class="stat-line"><span>Rating:</span><span>${anime.rating}</span></div>
            <div class="stat-line"><span>Ranking:</span><span>#${anime.mal_ranking}</span></div>
            <div class="stat-line"><span>Members:</span><span>${anime.members?.toLocaleString() || 'N/A'}</span></div>
            <div class="stat-line"><span>Genres:</span><span>${anime.genres?.join(', ') || 'N/A'}</span></div>
            <div id="stream-links"><em>Loading stream links...</em></div>
        `;
    }
    populateStats(selectedAnime);

    async function populateStreamLinks() {
        try {
            const res = await fetch(`/animes/${animeId}/links`);
            const data = await res.json();
            const streamContainer = document.getElementById('stream-links');
    
            const linksObj = data?.stream_links;
            if (!linksObj || Object.keys(linksObj).length === 0) {
                streamContainer.innerHTML = `<div class="stat-line"><span>Streams:</span><span>N/A</span></div>`;
                return;
            }
    
            const links = Object.entries(linksObj).map(
                ([site, url]) => `<div class="stream-link"><a href="${url}" target="_blank" rel="noopener noreferrer">${site}</a></div>`
            ).join("");
    
            streamContainer.innerHTML = `<div class="stat-line" style="flex-direction: column; align-items: flex-start;">
                                            <span>Streams:</span>
                                            <div class="stream-links-list">${links}</div>
                                        </div>`;
        } catch (err) {
            console.error("Failed to load stream links", err);
        }
    }
    
    
    populateStreamLinks();

    async function fetchMoreForums() {
        if (loading) return;
        loading = true;

        try {
            const cursorParam = dbCursor ? encodeURIComponent(dbCursor) : 0;
            const res = await fetch(`/animes/${selectedAnime.id}/forums?cursor=${cursorParam}`);
            if (!res.ok) throw new Error(`Failed to fetch forums: ${res.status}`);
            const data = await res.json();
            dbCursor = data.cursor;
            appendForums(data.forums);
        } catch (e) {
            console.error("Error fetching forums", e);
        } finally {
            loading = false;
        }
    }

    function appendForums(forums) {
        const container = document.getElementById('forum-container');
        if (!container) return;
        forums.forEach(forum => {
            const div = document.createElement('div');
            div.className = 'forum-post';
            div.innerHTML = `
                <h3>${forum.name}</h3>
                <p>${forum.description || 'No description provided.'}</p>
                <div class="forum-meta">
                    <span><strong>Posts:</strong> ${forum.posts}</span>
                    <span><strong>Subscribers:</strong> ${forum.subscribers}</span>
                    <span><strong>Created:</strong> ${forum.epoch || 'N/A'}</span>
                    <span><strong>Admins:</strong> ${forum.admin_count || 'N/A'}</span>
                </div>
            `;

            div.addEventListener('click', async () => {
                sessionStorage.setItem('selectedForum', JSON.stringify(forum));
                window.location.href = `/view/forum/${forum.name}`;
            })
            container.appendChild(div);
        });
    }

    function onScroll() {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 400;
        if (nearBottom) fetchMoreForums();
    }

    window.addEventListener('scroll', onScroll);
    // document.getElementById("forum-section").addEventListener('scroll', onScroll);

    fetchMoreForums();

    const makeForumBtn = document.getElementById('forum-make-btn');

    const modal = document.getElementById('forum-modal');
    const closeBtn = document.querySelector('.forum-close-btn');
    const submitBtn = document.getElementById('forum-submit');
    const toast = document.getElementById('forum-toast');
    
    makeForumBtn.addEventListener('click', () => {
        modal.classList.remove('hidden');
    });
    
    closeBtn.addEventListener('click', () => {
        modal.classList.add('hidden');
    });
    
    submitBtn.addEventListener('click', async () => {
        const name = document.getElementById('forum-name').value.trim();
        const desc = document.getElementById('forum-desc').value.trim();
    
        if (!name) {
            alert("Forum name is required.");
            return;
        }
    
        try {
            const response = await fetch("/forums", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    forum_name: name,
                    anime_id: animeId,
                    ...(desc ? { desc } : {})
                })
            });
    
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.description || "Something went wrong.");
            }
    
            // success
            modal.classList.add('hidden');
            showToast();
    
        } catch (error) {
            alert(error.message);
        }
    });
    
    function showToast() {
        toast.classList.remove('hidden');
        setTimeout(() => {
            toast.classList.add('hidden');
        }, 2500);
    }
});
