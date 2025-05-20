document.addEventListener('DOMContentLoaded', () => {
    const forumID = document.querySelector('meta[name="forumID"]').getAttribute('value');

    const contentWrapper = document.querySelector('.posts-scrollable');

    // Sorting/Filtering Controls
    const sortSelect = document.querySelector('#sort-select');
    const timeSelect = document.querySelector('#timeframe-select');

    let dbCursor = 0;
    let isFetching = false;
    let allPosts = [];
    let init = true;
    let currentSort = '0';
    let currentTime = '5';

    async function fetchMorePosts(reset = false) {
        if (isFetching) return;
        isFetching = true;

        try {
            const cursorParam = dbCursor ? encodeURIComponent(dbCursor) : 0;
            const url = `/forums/${forumID}/posts?cursor=${cursorParam}&sort=${currentSort}&timeframe=${currentTime}`;

            const response = await fetch(url);
            if (!response.ok) {
                const errorText = await response.text();
                throw new Error(`Status: ${response.status}\n${errorText}`);
            }

            const data = await response.json();
            dbCursor = data.cursor;

            if (reset) {
                allPosts = [...data.posts];
                contentWrapper.innerHTML = '';
            } else {
                allPosts.push(...data.posts);
            }

            if (init && (!data.posts || data.posts.length === 0)) {
                window.removeEventListener('scroll', onScroll);
                contentWrapper.innerHTML = `<h1 style='margin: 0 auto;'>Awfully quiet here...</h1>`;
            }

            renderPosts(data.posts);
            init = false;
        } catch (err) {
            console.error("Fetch error:", err);
        } finally {
            isFetching = false;
        }
    }

    function renderPosts(posts) {
        for (const post of posts) {
            const card = document.createElement('div');
            card.className = 'post-card';

            card.innerHTML = `
                <div class="post-header">
                    <span class="post-author">${post.username}</span>
                    <span class="post-date">${post.epoch}</span>
                </div>
                <h2 class="post-title">${post.title}</h2>
                <p class="post-body">${post.body_text}</p>
                <div class="post-actions">
                    <button class="vote-btn">⬆️</button>
                    <span class="score-count">${post.score}</span>
                    <button class="vote-btn">⬇️</button>
                    <button class="comment-btn">💬 ${post.comments}</button>
                    <button class="save-btn">💾</button>
                </div>
            `;

            card.addEventListener('click', () => {
                window.location.href = `/view/post/${post.id}`;
            });

            contentWrapper.appendChild(card);
        }
    }

    function onScroll() {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 400;
        if (nearBottom) fetchMorePosts();
    }

    function onSortChange() {
        currentSort = sortSelect.value;
        resetFetch();
    }

    function onTimeChange() {
        currentTime = timeSelect.value;
        resetFetch();
    }

    function resetFetch() {
        dbCursor = 0;
        init = true;
        contentWrapper.innerHTML = '';
        fetchMorePosts(true);
    }

    // Attach listeners
    sortSelect.addEventListener('change', onSortChange);
    timeSelect.addEventListener('change', onTimeChange);

    fetchMorePosts();
    window.addEventListener('scroll', onScroll);
});
