document.addEventListener('DOMContentLoaded', () => {
    const commentBar = document.getElementById('comment-bar');
    const commentsContainer = document.querySelector('.comments-container');
    let postID = window.location.pathname.split('/').filter(x => x).pop();
    let cursor = '0';
    let isLoading = false;
    let reachedEnd = false;

    const makeCommentCard = ({ id, author, body, created_at, edited, flair }) => {
        const card = document.createElement('div');
        card.classList.add('comment-card');
        card.innerHTML = `
            <div class="comment-header">
                <strong>${author}</strong>
                ${flair ? `<span class="flair">${flair}</span>` : ''}
                ${edited ? `<em class="edited">(edited)</em>` : ''}
                <span class="timestamp">${new Date(created_at).toLocaleString()}</span>
            </div>
            <div class="comment-body">${body}</div>
        `;
        return card;
    };

    const loadComments = async () => {
        if (isLoading || reachedEnd) return;
        isLoading = true;
        try {
            const res = await fetch(`/posts/${postID}/comments?cursor=${cursor}`);
            const data = await res.json();

            if (cursor === '0') {
                commentsContainer.innerHTML = ''; // clear "No Comments" on first load
            }

            if (data.comments) {
                data.comments.forEach(comment => {
                    commentsContainer.appendChild(makeCommentCard(comment));
                });
            }

            cursor = data.cursor;
            reachedEnd = data.end;
        } catch (err) {
            console.error("Failed to load comments:", err);
        } finally {
            isLoading = false;
        }
    };

    const postComment = async (body) => {
        try {
            const res = await fetch(`/posts/${postID}/comment`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({ body })
            });

            if (!res.ok) throw new Error("Post failed");

            const newComment = await res.json();
            const card = makeCommentCard(newComment);
            commentsContainer.prepend(card);
            commentBar.value = '';
        } catch (err) {
            alert("Could not post comment.");
            console.error(err);
        }
    };

    commentBar.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && commentBar.value.trim()) {
            e.preventDefault();
            postComment(commentBar.value.trim());
        }
    });

    window.addEventListener('scroll', () => {
        const nearBottom = window.innerHeight + window.scrollY >= document.body.offsetHeight - 300;
        if (nearBottom) loadComments();
    });

    loadComments(); // Initial load
});