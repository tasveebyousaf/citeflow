"""Designed post images and carousels."""
import cards

V = {"kicker": "NEW RESEARCH", "stat_value": "88.8%", "stat_label": "average accuracy",
     "key_points": ["Locates cells", "Designed to assist experts", "Tested on 339 smears"], "cta": "Read the study"}


def test_post_image_formats():
    for size in ((1080, 1080), (1200, 675), (1080, 1350)):
        img = cards.post_image(V, "AI that helps screen Pap smears", "University of Debrecen", None, size)
        assert img.size == size


def test_post_image_animation_frames_differ():
    a = cards.post_image(V, "Title", "Uni", None, (540, 540), t=0.2)
    b = cards.post_image(V, "Title", "Uni", None, (540, 540), t=3.0)
    assert a.tobytes() != b.tobytes()


def test_carousel_slides():
    slides = cards.carousel(V, "Title", "Uni")
    assert len(slides) == 1 + 3 + 1                                   # cover, one per fact, closing
    assert all(s.size == (1080, 1350) for s in slides)
