"""Connector adapters.

To register connectors implemented in this module, it is imported in
gaphor.adapter package.
"""

from __future__ import annotations

import functools
import itertools
from collections.abc import Iterator
from typing import Protocol, TypeVar

from gaphas.connections import Connection
from gaphas.connector import ConnectionSink, Handle, Port
from gaphas.connector import Connector as ConnectorAspect
from generic.multidispatch import FunctionDispatcher, multidispatch

from gaphor.core.modeling import Base, Diagram, Presentation
from gaphor.core.modeling.event import RevertibleEvent
from gaphor.core.modeling.properties import association, redefine, relation
from gaphor.diagram.copypaste import copy, paste
from gaphor.diagram.presentation import ElementPresentation, LinePresentation
from gaphor.diagram.support import get_diagram_item_metadata, get_model_element

T = TypeVar("T", bound=Base)


class ConnectorProtocol(Protocol):
    def __init__(
        self,
        element: Presentation,
        line: Presentation,
    ) -> None: ...

    def allow(self, handle: Handle, port: Port) -> bool: ...

    def connect(self, handle: Handle, port: Port) -> bool: ...

    def disconnect(self, handle: Handle) -> None: ...


class BaseConnector:
    """Connection adapter for Gaphor diagram items.

    Line item ``line`` connects with a handle to a connectable item ``element``.

    :param line: connecting item
    :param element: connectable item

    By convention the adapters are registered by (element, line) -- in that order.
    """

    def __init__(
        self,
        element: Presentation[Base],
        line: Presentation[Base],
    ) -> None:
        self.element = element
        self.line = line

        # Try to find the diagram. Any diagram can be None, when called while an element is unlinking
        assert (
            element.diagram is line.diagram
            or element.diagram is None
            or line.diagram is None
        )
        self.diagram: Diagram = element.diagram or line.diagram
        assert self.diagram

    def get_connected(self, handle: Handle) -> Presentation[Base] | None:
        """Get item connected to a handle."""
        if cinfo := self.diagram.connections.get_connection(handle):
            return cinfo.connected  # type: ignore[no-any-return]
        return None

    def allow(self, handle: Handle, port: Port) -> bool:
        """Determine if items can be connected.

        Is the connection allowed at all (during mouse movement for example)?

        Returns `True` if connection is allowed.
        """
        return bool(self.element.diagram) and self.element.diagram is self.line.diagram

    def connect(self, handle: Handle, port: Port) -> bool:
        """Connect to an element.

        Establish a connection between element and line. Also takes care of
        disconnects, if required (e.g. 1:1 relationships).

        Note that at this point the line may be
        connected to some other, or the same element. The connection at model
        level also still exists.

        Returns `True` if a connection is established.
        """
        return True

    def disconnect(self, handle: Handle) -> None:
        """Disconnect model level connections.

        Break connection, called when dropping a handle on a
        point where it can not connect.
        """


class NoConnector:
    def __init__(
        self,
        element,
        line,
    ) -> None:
        pass

    def allow(self, handle: Handle, port: Port) -> bool:
        return False

    def connect(self, handle: Handle, port: Port) -> bool:
        return False

    def disconnect(self, handle: Handle) -> None:
        pass


# Work around issue https://github.com/python/mypy/issues/3135 (Class decorators are not type checked)
# This definition, along with the ignore below, seems to fix the behaviour for mypy at least.
Connector: FunctionDispatcher[type[ConnectorProtocol]] = multidispatch(object, object)(
    NoConnector
)


@functools.lru_cache
def can_connect(parent, element_type) -> bool:
    parent_type = type(parent)
    get_registration = Connector.registry.get_registration
    for t1, t2 in itertools.product(parent_type.__mro__, element_type.__mro__):
        if r := get_registration(t1, t2):
            return r is not NoConnector
    return False


class RelationshipConnect(BaseConnector):
    """Base class for relationship connections, such as associations,
    dependencies and implementations.

    Unary relationships are allowed to connect both ends to the same element.

    This class introduces a new method: `relationship()`, which is used to
    find an existing relationship in the model that does not yet exist
    on the diagram.
    """

    element: Presentation
    line: LinePresentation[Base]

    def __init__(self, element: Presentation[Base], line: Presentation[Base]) -> None:
        super().__init__(element, line)
        self.copy_buffer = dict(copy(line.subject)) if line.subject else {}

    def relationship(
        self, required_type: type[T], head: relation, tail: relation
    ) -> T | None:
        """Find an existing relationship in the model that meets the required
        type and is connected to the same model element the head and tail of
        the line are connected to.

        type - the type of relationship we're looking for
        head - tuple (association name on the line, association name on the element)
        tail - tuple (association name on the line, association name on the element)
        """
        assert isinstance(head, association | redefine), f"head is {head}"
        assert isinstance(tail, association | redefine), f"tail is {tail}"

        line = self.line

        line_head = self.get_connected(line.head)
        line_tail = self.get_connected(line.tail)
        assert line_head
        assert line_tail
        head_subject = line_head.subject
        tail_subject = line_tail.subject

        # First check if the right subject is already connected:
        if (
            line.subject
            and head.get(line.subject) is head_subject
            and tail.get(line.subject) is tail_subject
        ):
            return line.subject  # type: ignore[return-value]

        # Try to find a relationship, that is already created, but not
        # yet displayed in the diagram on the tail side, since tail should
        # have a reference to head at least.
        if not (head_subject and tail.opposite):
            return None

        gen: Base
        for gen in getattr(tail_subject, tail.opposite):
            if not isinstance(gen, required_type):
                continue

            gen_head = head.get(gen)
            try:
                if head_subject not in gen_head:
                    continue
            except TypeError:
                if gen_head is not head_subject:
                    continue

            # Check for this entry on line.diagram
            item: ElementPresentation | LinePresentation
            for item in gen.presentation:
                # Allow line to be returned. Avoids strange
                # behaviour during loading
                if item.diagram is line.diagram and item is not line:
                    break
            else:
                return gen
        return None

    def new_relation(self, type: type[T]) -> T:
        return self.line.model.create(type)

    def new_relation_from_copy(self, type: type[T]) -> T | None:
        if not self.copy_buffer:
            return None

        for e in paste_model(self.copy_buffer, self.diagram):
            if isinstance(e, type):
                return e

        raise AssertionError(
            f"Copied elements, but no {type} found ({self.copy_buffer})"
        )

    def relationship_or_new(self, type: type[T], head: relation, tail: relation) -> T:
        """Like relation(), but create a new instance if none was found."""
        relation = self.relationship(type, head, tail)
        if relation:
            return relation

        line = self.line
        relation = self.new_relation_from_copy(type) or self.new_relation(type)

        assert isinstance(relation, type)
        line_head = self.get_connected(line.head)
        line_tail = self.get_connected(line.tail)

        assert line_head
        assert line_tail
        head.set(relation, line_head.subject)
        tail.set(relation, line_tail.subject)

        # TODO uncomment the following after Dependency is made a subclass of PackageableElement
        # owning_package = line_tail.subject.owningPackage
        # relation.owningPackage = owning_package

        assert isinstance(relation, type)
        return relation

    def connect_connected_items(self, connections: None = None) -> None:
        """Cause items connected to ``line`` to reconnect, allowing them to
        establish or destroy relationships at model level."""
        line = self.line
        diagram = self.diagram

        # First make sure coordinates match
        diagram.connections.solver.solve()
        for cinfo in connections or diagram.connections.get_connections(connected=line):
            if line is cinfo.connected:
                continue
            adapter = Connector(line, cinfo.connected)
            assert adapter, f"No element to connect {line} and {cinfo.connected}"
            adapter.connect(cinfo.handle, cinfo.port)

    def disconnect_connected_items(self) -> list[Connection]:
        """Cause items connected to @line to be disconnected. This is necessary
        if the subject of the @line is to be removed.

        Returns a list of (item, handle) pairs that were connected (this
        list can be used to connect items again with
        connect_connected_items()).
        """
        line = self.line
        diagram = self.diagram

        # First make sure coordinates match
        diagram.connections.solver.solve()
        connections = list(diagram.connections.get_connections(connected=line))
        for cinfo in connections:
            adapter = Connector(cinfo.item, cinfo.connected)
            adapter.disconnect(cinfo.handle)
        return connections

    def connect_subject(self, handle: Handle) -> bool:
        """Establish the relationship at model level."""
        raise NotImplementedError("Implement connect_subject() in a subclass")

    def disconnect_subject(self, handle: Handle) -> None:
        """Disconnect the diagram item from its model element."""
        del self.line.subject

    def connect(self, handle: Handle, port: Port) -> bool:
        """Connect the items to each other.

        The model level relationship is created by create_subject()
        """
        if not super().connect(handle, port):
            return False
        opposite = self.line.opposite(handle)
        if self.get_connected(opposite):
            self.connect_subject(handle)
            line = self.line
            if line.subject:
                self.connect_connected_items()
        return True

    def disconnect(self, handle: Handle) -> None:
        """Disconnect model element."""
        line = self.line
        opposite = line.opposite(handle)
        oct = self.get_connected(opposite)
        hct = self.get_connected(handle)

        if hct and oct:
            # Both sides of line are connected => disconnect
            self.disconnect_connected_items()
            self.disconnect_subject(handle)

        super().disconnect(handle)


class DirectionalRelationshipConnect(RelationshipConnect):
    """Base for relationship connections between unique elements."""

    def allow(self, handle: Handle, port: Port) -> bool:
        """In addition to the normal check, both relationship ends may not be
        connected to the same element.

        Same goes for subjects.
        """
        opposite = self.line.opposite(handle)
        element = self.element
        connected_to = self.get_connected(opposite)

        # Element can not be a parent of itself.
        if connected_to is element:
            return False

        # Same goes for subjects:
        if (
            connected_to
            and not connected_to.subject
            and not element.subject
            and connected_to.subject is element.subject
        ):
            return False

        return super().allow(handle, port)


@Connector.register(ElementPresentation, LinePresentation)
class MetadataRelationConnect(DirectionalRelationshipConnect):
    """A generic relationship connector."""

    def allow(self, handle, port):
        if not super().allow(handle, port):
            return False

        element = self.element
        opposite_element = self.get_connected(self.line.opposite(handle))
        if metadata := get_diagram_item_metadata(type(self.line)):
            return (
                (
                    isinstance(element.subject, metadata["head"].type)
                    and (
                        not opposite_element
                        or isinstance(opposite_element.subject, metadata["tail"].type)
                    )
                )
                if handle is self.line.head
                else (
                    isinstance(element.subject, metadata["tail"].type)
                    and (
                        not opposite_element
                        or isinstance(opposite_element.subject, metadata["head"].type)
                    )
                )
            )

        return False

    def connect_subject(self, handle):
        subject_type = get_model_element(type(self.line))
        metadata = get_diagram_item_metadata(type(self.line))
        if not metadata:
            return False

        self.line.subject = self.relationship_or_new(
            subject_type, metadata["head"], metadata["tail"]
        )
        return True


def paste_model(copy_data, diagram) -> Iterator[Base]:
    new_elements: dict[str, Base] = {}

    def create(ref: str):
        if ref in new_elements:
            return new_elements[ref]

        if ref in copy_data:
            paster = paste(copy_data[ref], diagram, create)
            new_elements[ref] = next(paster)
            next(paster, None)
            return new_elements[ref]

    for old_id in copy_data:
        if old_id in new_elements:
            continue
        create(old_id)

    for element in new_elements.values():
        assert element
        element.postload()

    return iter(new_elements.values())


class ItemConnected(RevertibleEvent):
    def __init__(self, element, handle, connected, port):
        super().__init__(element)
        self.handle_index = element.handles().index(handle)
        self.connected_id = connected.id
        self.port_index = connected.ports().index(port)

    def revert(self, target):
        # Reverse only the diagram level connection.
        # Associations have their own handlers
        connections = target.diagram.connections
        handle = target.handles()[self.handle_index]

        connector = ConnectorAspect(target, handle, connections)
        connector.disconnect_handle()


class ItemDisconnected(RevertibleEvent):
    def __init__(self, element, handle, connected, port):
        super().__init__(element)
        self.handle_index = element.handles().index(handle)
        self.connected_id = connected.id
        self.port_index = connected.ports().index(port)

    def revert(self, target):
        # Reverse only the diagram level connection.
        # Associations have their own handlers
        handle = target.handles()[self.handle_index]
        connections = target.diagram.connections
        connected = target.diagram.lookup(self.connected_id)

        sink = ConnectionSink(connected)
        sink.port = connected.ports()[self.port_index]
        connector = ConnectorAspect(target, handle, connections)
        connector.connect_handle(sink)


class ItemTemporaryDisconnected(RevertibleEvent):
    def __init__(self, element, handle, connected, port):
        super().__init__(element)
        self.handle_index = element.handles().index(handle)
        self.connected_id = connected.id
        self.port_index = connected.ports().index(port)

    def revert(self, target):
        handle = target.handles()[self.handle_index]
        connections = target.diagram.connections
        connected = target.diagram.lookup(self.connected_id)

        sink = ConnectionSink(connected)
        sink.port = connected.ports()[self.port_index]
        connector = ConnectorAspect(target, handle, connections)
        connector.reconnect_handle(sink)


class ItemReconnected(RevertibleEvent):
    def __init__(self, element, handle, connected, port):
        super().__init__(element)
        self.handle_index = element.handles().index(handle)
        self.connected_id = connected.id
        self.port_index = connected.ports().index(port)

    def revert(self, target):
        connections = target.diagram.connections
        handle = target.handles()[self.handle_index]

        cinfo = connections.get_connection(handle)
        connections.solver.remove_constraint(cinfo.constraint)
        target.handle(
            ItemTemporaryDisconnected(target, handle, cinfo.connected, cinfo.port)
        )
