import gaphas
import pytest

from gaphor.core import event_handler
from gaphor.core.modeling import Base, Diagram, ElementDeleted, Presentation
from gaphor.core.modeling.properties import association


class Example(gaphas.Element, Presentation):
    def __init__(self, diagram, id):
        super().__init__(connections=diagram.connections, diagram=diagram, id=id)
        self._test_unlinked = False

    @property
    def test_unlinked(self):
        return self._test_unlinked

    def unlink(self):
        self._test_unlinked = True
        super().unlink()


class ExampleLine(gaphas.Line, Presentation):
    def __init__(self, diagram, id):
        super().__init__(connections=diagram.connections, diagram=diagram, id=id)
        self._test_unlinked = False

    @property
    def test_unlinked(self):
        return self._test_unlinked

    def unlink(self):
        self._test_unlinked = True
        super().unlink()


class ModelElement(Base):
    diagram = association("association", Diagram, composite=True)


@pytest.fixture
def diagram(element_factory):
    return element_factory.create(Diagram)


def test_diagram_can_be_used_as_gtkview_model():
    diagram = Diagram("id", None)

    assert isinstance(diagram, gaphas.model.Model)


def test_canvas_is_saved():
    diagram = Diagram("id", None)
    saved_keys = []
    diagram.save(lambda name, val: saved_keys.append(name))

    assert "canvas" not in saved_keys


def test_canvas_item_is_created(element_factory):
    diagram = element_factory.create(Diagram)
    example = diagram.create(Example)

    assert example in diagram.get_all_items()
    assert example.diagram is diagram


def test_canvas_is_unlinked(element_factory):
    diagram = element_factory.create(Diagram)
    example = diagram.create(Example)

    diagram.unlink()

    assert example.test_unlinked


def test_can_only_add_diagram_items(element_factory):
    diagram = element_factory.create(Diagram)

    with pytest.raises(TypeError):
        diagram.create(Diagram)


class ViewMock:
    def __init__(self):
        self.removed_items = set()

    def request_update(self, items, removed_items) -> None:
        self.removed_items.update(removed_items)


def test_remove_presentation_triggers_view(element_factory):
    diagram = element_factory.create(Diagram)
    view = ViewMock()
    diagram.register_view(view)

    example = diagram.create(Example)

    example.unlink()

    assert example.diagram is None
    assert example not in diagram.ownedPresentation
    assert example in view.removed_items


def test_order_presentations_lines_are_last(diagram):
    example_line = diagram.create(ExampleLine)
    example = diagram.create(Example)

    assert list(diagram.get_all_items()) == [example, example_line]


def test_order_presentations_line_is_grouped(diagram):
    example_line = diagram.create(ExampleLine)
    example_1 = diagram.create(Example)
    example_2 = diagram.create(Example)

    example_line.parent = example_1

    assert list(diagram.get_all_items()) == [example_1, example_2, example_line]


def test_order_grouped_presentations(diagram):
    example_1 = diagram.create(Example)
    example_2 = diagram.create(Example)

    example_1.parent = example_2

    assert list(diagram.get_all_items()) == [example_2, example_1]


def test_unlink_presentations_before_diagram(diagram, event_manager):
    events = []

    @event_handler(ElementDeleted)
    def listener(event):
        events.append(event)

    event_manager.subscribe(listener)

    diagram.create(Example)
    diagram.create(Example)

    diagram.unlink()

    assert events[-1].element is diagram


def test_unlink_order_with_parent_element_in_diagram(
    diagram, event_manager, element_factory, sanitizer_service
):
    events = []

    @event_handler(ElementDeleted)
    def listener(event):
        events.append(event)

    event_manager.subscribe(listener)

    element = element_factory.create(ModelElement)
    example = diagram.create(Example, subject=element)
    element.diagram = diagram

    example.unlink()

    assert events[-1].element is not example
