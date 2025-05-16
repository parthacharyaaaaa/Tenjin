document.addEventListener('DOMContentLoaded', () => {
    const postID = window.location.pathname.split('/')[3];
    const deleteButton = document.getElementById('delete-btn')
    window.dependencyReady?.then(() => {
        // Delete button is dynamically rendered anyways, no need to check localStorage for login key
        if(deleteButton && deleteButton !== undefined){
            deleteButton.addEventListener('click', async () => {
                try{
                    const response = await fetch(`/posts/${postID}?redirect=1`, {
                        method:'DELETE',
                        credentials:'include'
                    });

                    if(!response.ok){
                        throw new Error('Failed to delete post, try again later');
                    }

                    const data = await response.json()
                    alert('Post deleted succesfully. This change may be reflected in some time');
                    window.location.href = data.redirect;
                }
                catch(error){
                    console.error(error)
                }
            });
        }
    });
});