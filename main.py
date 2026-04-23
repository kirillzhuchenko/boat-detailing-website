from flask import Flask, render_template
import os

app = Flask(__name__)

GALLERY_FOLDER = os.path.join(app.static_folder, "images", "gallery")
ALLOWED_EXTENSIONS = {".jpg", ".png"}

@app.route('/')

def home():

    images = get_gallery_images()
    return render_template('index.html', images=images)


def get_gallery_images():
    """
    Scans GALLERY_FOLDER and returns a sorted list of image filenames.
    Only .jpg and .png files are included.
    Files are sorted alphabetically by filename (A → Z).
    """
    if not os.path.exists(GALLERY_FOLDER):
        os.makedirs(GALLERY_FOLDER)  # Create the folder if it doesn't exist yet
        return []

    images = [
        f for f in os.listdir(GALLERY_FOLDER)
        if os.path.splitext(f)[1].lower() in ALLOWED_EXTENSIONS
    ]

    images.sort(key=lambda f: f.lower())  # Case-insensitive alphabetical sort
    return images

if __name__ == '__main__':
    app.run(debug=True)