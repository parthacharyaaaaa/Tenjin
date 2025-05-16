document.addEventListener('DOMContentLoaded', () => {
    const postID = window.location.pathname.split('/')[3];
    const upvoteButton = document.getElementById('upvote-btn');
    const downvoteButton = document.getElementById('downvote-btn');
    const voteCountSpan = document.getElementById('vote-count');
    let showChange = true;
    if (!voteCountSpan || voteCountSpan === undefined){
        showChange = false;
    }
    let voteCount = voteCountSpan.innerText
    if(isNaN(voteCount) || voteCount.includes(".")){
        showChange = false;
    }
    voteCount = parseInt(voteCount)

    let voteType = -1;

    async function unvotePost() {
        try {
            const response = await fetch(`/posts/${postID}/unvote`, {
                method: 'PATCH',
                credentials: 'include'
            });

            if (!response.ok) {
                throw new Error('Failed to unvote post :(');
            }

            upvoteButton.classList.remove('casted');
            downvoteButton.classList.remove('casted');

            if(voteType === 1 && showChange){
                voteCountSpan.innerText = --voteCount;
            }
            else if(voteType === 0 && showChange){
                voteCountSpan.innerText = ++voteCount;
            }

            voteType = -1;

        }
        catch (error) {
            console.error(error)
        }
    }

    async function votePost(castedVoteType){
        try{
            const response = await fetch(`/posts/${postID}/vote?type=${castedVoteType}`, {
                method:'PATCH',
                credentials:'include',
                body:JSON.stringify
            });

            if(!response.ok){
                throw new Error("Failed to vote on post :(");
            }

            voteType = castedVoteType;
            
            if(!voteType){
                upvoteButton.classList.remove('casted');
                downvoteButton.classList.add('casted');
                if(showChange){
                    voteCountSpan.innerText = --voteCount;
                }
            }
            else{
                downvoteButton.classList.remove('casted');
                upvoteButton.classList.add('casted');
                if(showChange){
                    voteCountSpan.innerText = ++voteCount;
                }
            }
            
        }
        catch(error){
            console.error(error)
        }
    }

    window.dependencyReady?.then(async () => {
        if (!localStorage.getItem('login')) {
            upvoteButton.addEventListener('click', () => {
                alert("Please login to vote on posts");
            })
            downvoteButton.addEventListener('click', () => {
                alert("Please login to vote on posts");
            });
        }

        else {
            /*
            -1: No vote casted
            0: Downvote
            1: Upvote
            Yeah, a bit counter-intuitive because -1 is no vote and not downvote. Shoutout to me when I was making the db models and decided to be 'clever' and store vote types as booleans in the post_votes table
            */
            try {
                const response = await fetch(`/posts/${postID}/is-voted`, {
                    method: 'GET',
                    credentials: 'include'
                });
                const data = await response.json();
                console.info(data)
                // Downvoted post
                if (data === 0) {
                    downvoteButton.classList.add('casted');
                }
                else if (data === 1) {
                    upvoteButton.classList.add('casted');
                }
            }
            catch (error) {
                console.error(error);
            }

            downvoteButton.addEventListener('click', async () => {
                voteType === 0 ? unvotePost() : votePost(0)
            });

            upvoteButton.addEventListener('click', async () => {
                voteType === 1 ? unvotePost() : votePost(1)
            });
        }
    })
})